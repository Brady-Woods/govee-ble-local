# Changelog

Notable changes to `govee-ble-local`. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/); the project follows
[Semantic Versioning](https://semver.org/) and is versioned in `pyproject.toml`. Entries are grouped
**Added / Changed / Fixed / Spec** (Spec = the Kaitai `spec/*.ksy` + `spec/devices.yaml` protocol
model, from which the shipped readers are generated).

## [Unreleased]

### Added
- **`bound_advertisement(advertisement_data, sku, *, fallback_name=None)`, in `__all__`.** It reads an
  advertisement's pact and encrypt flag from its Govee manufacturer data whatever the local name, for a caller
  that has no `Device` yet. It returns the public `GoveeAdvertisement` (`.pact`, `.encrypted`,
  `.encryption_known`), or None when the name isn't a Govee name and there's no Govee pact block.
  - **What it is:** the read `create_device`, `make_device` and the `Device` make of their advertisement since
    1.6.1, until now package-internal as `identify._bound_advertisement`. Its pact is the pact `create_device`
    routes the profile by. After a `GoveeBleNotSupported` it is the pact that was refused, even for a renamed or
    nameless Home Assistant record, where `identify()`'s name gate returns None.
  - **Labels:** `.sku` is always the caller's `sku`, upper-cased, whatever SKU a Govee name carries; `.name` is
    the advertised name, else `fallback_name`, else `""`. Neither changes the pact or the flag. Until now a
    Govee name's own SKU labelled the internal read; nothing read it.
  - **Merged records:** with more than one Govee pact entry, the first in the dict's order gives the pact and
    the flag, as `create_device` and the `Device` constructor take them.
  - **App basis, for the light goodsTypes:** the control screen matches a bound device by its stored MAC, not
    its name (`AbsViewMode4OpWithOtaCheck.s2:766-774`). It parses the broadcast with the stored goodsType
    (`AbsViewMode4OpWithOtaCheck.j1:570-591` -> `GoodsType.parseBleBroadcastPactInfo:1564-1578` ->
    `BleUtil.parseBleBroadcastPact:779-803`) and keeps the encrypt flag (`AbsViewMode4OpWithOtaCheck.w2:171-178`).
    The caller's `sku` stands for that stored device.
  - **What stays as it is:** `identify.identify` and `identify.parse_manufacturer_data`, the raw parser under
    this read, stay out of `__all__`; the bare `(encrypted, broadcast_version, pact_type, pact_code)` tuple
    can't grow a field without a MAJOR. Released Home Assistant integrations import both, so both keep their
    signatures and results through 1.x; a new way of reading a merged record goes in a new function.
    Discovery keeps the name gate as before.
- **`Device.advertised_on(advertisement_data) -> bool | None`: an advertisement's on/off, decoded as this device
  decodes it.** A light is on for a switch byte of 1, a plug for any bit of its profile's `advert_on_mask`. The
  result is None when the advertisement carries no switch byte (no Govee manufacturer data, a header below 2, too
  short), and always on the H6057, whose power is its light switch (below). It is passive: it leaves the state as it is and ignores the window after a command or read in which
  `ingest_advertisement` ignores the advertised on/off. `ingest_advertisement` now decodes through it.
  - **Why a Device method:** this mirrors the app's split. `BleUtil.parseBleBroadOnOff` reads the raw byte the
    same way for every device (the overload at `:719-744` for lights, the one at `:700-717` for plugs), and each
    device model decodes it in its own `z(scanRecord)`: `AbsBaseBleModel.z:393-397` for lights, h5080
    `Model.z:254-265` and `Model4Plug2.z:269-290` for plugs. So a caller no longer passes the mask itself. The
    Home Assistant integration can use it in place of `identify.parse_broadcast_onoff(..., on_mask=...)`, which
    is not in `__all__`.
  - **What stays:** `identify.parse_broadcast_onoff` and `DeviceProfile.advert_on_mask` are unchanged and stay
    supported through 1.x, because released integrations import them.
  - **Merged records:** the on/off comes from the Govee entry whose value last changed between two advertisements
    the Device was given, and from that entry alone. That is the entry the latest broadcast carried. When it has no
    switch byte, the advertisement gives no on/off. A key new in the record doesn't count as a change. Until one
    has changed, or when the record lacks that entry, the first Govee entry with a switch byte is read.
    `advertised_on` gives what `ingest_advertisement` would read from the same advertisement now, and stays
    passive. Only an ingest moves on the advertisement the next one is compared with. See Fixed, below.
- **`Device.power_read_at -> float | None`: when a read last returned the device's whole on/off.** The value is in
  `time.monotonic()` seconds, and only while `state.is_on` still holds what that read gave; otherwise None.
  - **What stamps it:** an answered 0xAC status read, a polled light's `aa 01` (on the H6057 the `aa 34`
    device-detail read that replaces it, its light flag: `DeviceProfile.detail_power_read`), a bar-switch
    device's `aa 36` (in a poll or before a bar write) and a plug's `aa 01` relay read. A poll that read nothing,
    a read discarded as stale, an advertisement and an acked write stamp nothing.
  - **What drops it:** any change to `is_on` that no read made, even one that later changes back. Also a power
    write or scene activation that raises (it may have reached the device), including a `set_scene_by_name`
    whose power-on lead or upload raises. Also a write that wakes a zoned device not known to be on, an acked
    zone-on while `is_on` is not True (an H60A6 `33 30` zone-on also sets the main switch, FUN_9b0278dc
    @0x9b02795e), and each case where `is_on` may say on for a device that is off. Those are the
    `DeviceState.zone_power_confirmed` events. A read issued before a drop doesn't stamp after it.
  - **Why it's needed:** `DeviceState.optimistic` clears at the first answered read and is never set again, so
    a consumer could not tell a later poll that read the on/off from one that read nothing.
  - **Why on the Device:** it is not a `DeviceState` field, so `dataclasses.asdict(state)` and `snapshot()`
    don't carry it. A consumer that compares `asdict(state)` between polls (the Home Assistant integration
    does) sees no change from a re-read.
- **158 more SKUs, traced from the app's device list** (spec rows; no library code changed but three row facts,
  below: segment colour temperature, the segment-brightness groups and a status row's device-info reads; the
  illumination-tab pair that promotes H60A4 / H80A4, the aa 40 section sum that promotes H61A9, the segment split
  that promotes H61D2-H61D6, the live piece count that promotes 14 h705a SKUs and, by the user's decision on its
  first walk, H608A-D / H808A, the status rows' IC reads that promote H3401 and H3510 / H3511, the per-SKU support
  level that promotes five h6609 260 SKUs, the colour page's Kelvin-tint read-back that promotes the six h70b3
  SKUs, the user's decision on their 7.5.12 trace that promotes H604B / H604D, the user's decision on parity with
  H7092 that promotes H7090, the empty secret check that promotes H6058, and the per-pact support level that
  promotes H611A / H6143 / H6144 at pact (2,1), below): 145 admitted by default (`app_traced`) and 13 behind
  `allow_modelled` (`modelled`).
  The entries below admit 127 more by default (16 through the resolver changes; H7004, H1162 / H1163 / H1167 /
  H1168, H70B1 and H70BC; 17 through the status-read command ids and the BK pact gate; the 10 SKUs of the
  `legacy_02` colour scheme; H7061; H70B2, H6630, H8630 and H6631 on the device-list card; H7070 on its pact's
  status read; H6071 / H6075 on the `kelvin_v9` scheme; H615A-E, whose pairing write is read as an exact-pact
  condition; H6057 on its light switch; through the live piece count H1A45 and the five h61d3 SKUs behind
  `allow_modelled` since 1.5.0, H61B8 / H61B9 / H61F2 / H61F5 / H61F6; through the status rows' IC reads H702A /
  H702B / H702C, H7026 / H8026, H7027 and H30D0 / H30D1; through the 0xA3 colour unit H3010 / H3011 / H3012 / H3013;
  through the aa 0f string count the nine h7020 bulb-string SKUs; the six H707A strings through the status read's
  segment count; the 20 dreamcolorlightv1 71 / 123 SKUs through the per-SKU connect reads; four Glide panels at
  the layout editor's cap and H6063 at the 20 panels its colour page drives; H60A1 / H80A1 through the numeric
  Kelvin compare; and H7072 and H61E5 / H61E6 on parity with admitted rows that share their one gap) and 7 more
  behind `allow_modelled` (H6119, H6609 and H706A / H706B / H706C / H806A / H806C), for 371 admitted by default and
  20 behind `allow_modelled` in all (with H8015, a 1.6.1 SKU
  now admitted by default: Changed). None is
  hardware-verified (the only bench check, below, was read-only), and none has a bundled scene catalog, so none
  is offered scenes. Each is listed with its family and goodsType; † marks a SKU no row lists that the
  catalogue snapshot (`spec/govee_catalog.json`) routes to that row by its goodsType alone (16 of them).

  Admitted by default:
  - stringlightv2: **H6121 / H6141 / H6142 / H6154 / H6195** (1), **H6170 / H6196**, **H613G**† (2),
    **H616B** (68), **H6178** (78), **H6192** (143);
  - h6114: **H6114** (39); h613839: **H6138 / H6139** (39), each on an advertised pact only (below);
    carlightv1: **H6118 / H6194** (6); tvlightv1: **H6179** (12); pact_tvlightv2: **H6198 / H6199** (24), on
    an advertised pact only (below);
  - h60ax: **H60A4 / H80A4** (302, by the illumination-tab pair, below); h6047: **H6039** (277); h61d3:
    **H61B0** (264, with H61B3 / H61B6), **H61D2 / H61D3 / H61D4 / H61D5 / H61D6** (202, by the segment
    split, below); dreamcolorlightv1: **H70A1 / H70A2 / H70A3** (193),
    **H61A9** (204, by the aa 40 section sum, below);
  - rgbiclight: **H61C2 / H61C3 / H61C5** (141), **H6671 / H6672** (332), **H617G**, **R617G**† (351),
    **H618G**, **R618D**† (352);
  - hollowlamp: **H6050** (27), **H6051** (37), **H6055** (42), **H6073** (58), **H6059** (84), **H6058** (59,
    with the empty secret check, below); tablelampv1:
    **H6078** (128); straightfloorlamp: **H6076 / H8076**, **R16D0**† (69), on an advertised pact only (below),
    **H16C0** (377);
  - h6022: **H6022 / H8022** (249); chassislightv1: **H7092** (162); h7090: **H7090** (44), with the colour
    read-back gap it shares with H7092 (Spec, below);
  - dreamcolorlightv2: **H611A / H6143 / H6144** (21), at pact (2,1) only, by its per-pact support level (below;
    at (1,1) / (1,2) and with no pact they stay behind `allow_modelled`);
  - h6840: **H6840**, **H8840**† (278), **H6841**, **H8841**† (325), **H6842**, **R6842**† (381), **H6843**
    (382);
  - h6609: **H70C1 / H70C2 / H70CB** (181), **H70C9 / H7084 / H6871 / R6871**, **H80C5**† (260, by the per-SKU
    support level, below), **H6860 / R6860 / H6861 / R6861** (362), **H6350** (374), **H6850** (378),
    **H6351** (392);
  - pact_h605b: **H605C** (120), **H6602** (142), **H6604 / H8604** (243), **H2A40**, **H2A41**† (360);
    pact_h6099: **H6099** (191), **H6097** (273), **H66A0**, **H66A1 / R2A80 / R2A81**† (327);
  - h7057: **H7057**, **H8057**† (274), **H7058** (275), **H7093** (289), **H7094** (290), **H8066** (305),
    **H7087** (315), **H3501** (339), **H3500** (345), **H3200** (353), **H8067** (393);
  - h7020: **H7025**, **H8025**† (296); h7045: **H7045 / H7046** (338); h7075: **H7075** (205); h7078:
    **H7078** (245); h7086: **H7086** (293); h70b6: **H70B6** (329), on an advertised pact only (below);
    h6800: **H6800** (272); h3751: **H3751** (354);
  - h70b3: **H70B3** (229), **H70B4** (230), **H70B5** (231), **H6810** (255), **H6811**, **H8811**† (256), by the
    colour page's Kelvin-tint read-back (Changed, below), each on an advertised pact only (below);
  - h604a: **H604B** (109), **H604D** (133), on their Govee Home 7.5.12 trace, by the user's decision: 7.7.02 runs
    their screen from the on-demand module `pact_h604a` (`pfd/Config`), which the 7.7.02 capture doesn't hold, so
    the shipped path is unseen (Spec, below);
  - h7017: **H7017** (0), a grow light: power and brightness only, served at any pact, as its screen runs no
    pact test (`h7017 Model.t`);
  - h705a: **H705A / H705B / H705C / H801B** (125), **H705D / H705E / H705F / H805A / H805B / H805C / H805D**
    (240), **H7067 / H7068 / H7069** (250), by the live piece count, and **H608A / H608B / H608C / H608D /
    H808A** (184), by the live piece count and the user's decision on its first walk (below);
  - h705a: **H3401** (394); h7057: **H3510**, **H3511**† (349), by the status rows' IC reads (below).

  Behind `allow_modelled` (the facts are traced, but the library doesn't yet do everything the app does on
  every unit; each row's `doc` names the difference):
  - h60ax: **H1270** (346), **H1232** (356), **H1252** (373);
  - h61d3: **H703A / H703B** (263);
  - homelightv1: **H6148** (16); h1741: **H1741 / R1741** (365);
  - h6609: **H70C4 / H70C5 / H70C7 / H6870 / H80C4** (260, on their IC-gated read-back, below).
- **H6069 / H8069**† (h6069 284), **H3700** (h3700 383) and **H7076** (h7076 313), admitted by default
  (`app_traced`). Each is a status-read row whose screen takes its live power from the aa 01 keep-alive reply
  (`power_live: [heartbeat_aa01]`), which the resolver now serves on a status row as it does on a polled one
  (below), and which the library follows (`heartbeat_power`, below). Not hardware-verified (none is on the bench).
- **H70D1 / H80D1**† (h70dx 267), **H70D2** (268) and **H70D3** (269), admitted by default (`app_traced`). Their
  screen's connect read is the bare 0xAC status read `ac 03 00`, which requests no TLV type
  (`VM4H70DX.e5`), and it parses each TLV the reply carries, the 0xA5 colour groups included
  (`VM4H70DX.g5`). Which TLVs the firmware returns is not in the app; not hardware-verified.
- **H14C0 / H14A1 / R14A1 / H14C2 / R14C2 / H14C1 / R14C0 / R14C1** (bulblightv3 342), admitted by default
  (`app_traced`). After its connect reads the screen sends `33 25 01` (light_effect_stop) only for H14A1 /
  R14A1 / H14C2 / R14C2 (bulblight `Support.c`, `VM4Light.i5`), so those four SKUs carry it as a `by_model`
  connect write and the other four send none. Not hardware-verified (none is on the bench).
- **Six more SKUs whose status read names TLV types the spec now has ids for** (Spec, below), all by default:
  **H7071** (h6092 334), **H7073** (333) and **H3860** (401), power and brightness; **H3351** (h7072 371),
  **H70B8** (h70b6 341) and **H7072** (h7072 294), power, brightness, RGB and colour temperature. Each sends the
  app's request: `ac 03 02 41 0c`, `ac 03 03 41 0c 42`, `ac 03 03 41 0c 45`, `ac 03 03 41 a5 4b` and
  `ac 03 04 41 39 7d 7e` (`VM4LightH7070.T4`, `VM4LightH3860.T4`, `H7072VM4Light`, `VM4H70B6.i5`). H7072 is
  `app_traced` with one gap named, as H3351 is (the user's decision D5, 2026-10-22): the app places each TLV 0xA5
  group at (group - 1) x that TLV's own record count (`SubMode4Color.j`), the library at (group - 1) x 4, and its
  firmware's split is untraced; the admitted H6860 and H60B1 status rows share it. None is hardware-verified.
- **The BK-firmware rows, on an advertised pact only.** The app rewrites these SKUs to a BK goodsType only for a
  record whose pact meets the gate, and keeps a record without a pact (an int 0) at its source goodsType
  (`SkuToGoodsTypeUtil.f` over `OldRgbBkUtil.a` / `.b`; `OldRgbicBkUtil.isRgbicBk4Ble`; `OldCarBkUtil.isBkCar`),
  so the library takes such a target row only on an advertised pact at or above the gate (`profile_for`,
  `make_device`, `create_device` and `is_supported_sku`, whatever the goodsType). Each of these rows was held
  for exactly that; nothing else on them changed:
  - by default: h6159 **H6159 / H6110 / H6109 / H614B / H614E**, h6160 **H6160** and h6182 **H6182** (40, pact
    (2,1)); h6113 **H6113** and h6181 **H6181** (39, (10,1) / (10,2)); barelightv1 **H6145 / H6146 / H6147**
    (17, (10,1) to (10,3));
  - behind `allow_modelled`: h6119 **H6119** (44, (10,1) / (10,2));
  - the BK route of three SKUs above, carlightv1 **H6118 / H6194** and tvlightv1 **H6179** (39,
    (10,1) / (10,2), percent brightness): without a pact, and at their own pacts, they keep their
    goodsType-6 / -12 rows.

  These twelve, H6119 and H6114 / H6138 / H6139 have no admitted source row, so `supported_skus()` lists them,
  but `is_supported_sku(sku)`, `profile_for`, `make_device` and `create_device` refuse one without a pact:
  pass the advert's (`is_supported_sku(sku, pact=adv.pact)`; `scanner.supported()`, `discover()` and
  `create_device` with the advertisement already do). Not hardware-verified: no gated SKU is on the bench.
- **H6076 / H8076 / R16D0**† (straightfloorlamp 69) **on an advertised pact only**, as the BK rows above. The
  app accepts (1,1) and (2,1) for goodsType 69, and at (1,1) the screen runs the polled connect read
  (`VM4Light.d0` -> `VM4Light$connectBleSuc$1`), not the 0xAC status read the row serves, which it runs at
  pactType 2 only (`Support.isH6076Iteration`). A unit that advertises no pact may be a (1,1) one, so the row is
  `pact_required` (Spec, below): `profile_for`, `is_supported_sku`, `make_device` and `create_device` refuse
  these three without a pact, rather than serve a pact-less record the (2,1) status profile with the plaintext
  cipher fallback. `create_device` with the advertisement still builds them: an encrypted (2,1) advert, as both
  bench units send, gives the status profile and AES (checked offline, not on the bench). They join the
  pact-gated SKUs above, 19 in all with H6119, and 36 with H611A / H6143 / H6144, the six h70b3 SKUs, H70B6,
  H6198 / H6199 and H615A-E (below; `supported_skus()` lists them; a caller passes the advert's pact), so the
  integration's setup fix for those covers them too.
- **H611A / H6143 / H6144 (dreamcolorlightv2 21) on an advertised pact (2,1) only, by a per-pact support level**
  (the user's decision D6 (a), 2026-10-22). Their screen, the rgbiclight AcNewDetail, picks its colour frames,
  read-back and brightness range from per-unit gates the library can't follow: `Support4DreamColorLightV2.a:66-83`
  (both versions read, and the hard version not a Telink one, or hard >= 1.00.01 with soft >= 1.06.00) gives
  colour version 0, the h60a6 frames and the aa a5 read-back the row states (`Config.Av:8961-8964`), and
  `BrightnessConfig.Config.h2:1321-1324` gives percent brightness on non-Telink hardware. At (2,1) both take the
  row's branch: (2,1) is the BK protocol for goodsType 21 (dreamcolorlightv2 `Support.isBKProtocol:375-383` ->
  `e:241-246`), and a BK unit's hard version is no Telink one (`OtaType.parseHardVersion:74-118`: Telink is major
  1, BK major 2). barelightv1 17 (H6145-7) was admitted on the same inference. At (1,1) / (1,2) a unit may be
  Telink, so those pacts, and a record with no pact, stay `modelled`.
  - **How:** a devices.yaml pact branch may carry `support` (Spec, below), the row's level at that pact alone.
    The library routes the SKU at each pact at its level and, without a pact, at the row's own: by default
    `profile_for`, `is_supported_sku`, `make_device` and `create_device` take these three only with pact (2,1),
    and `allow_modelled` adds (1,1), (1,2) and no pact, with the same facts. `supported_skus()` lists them.
  - Their facts are those of the released H617C / H617E / H617F / H6168 / H6169 profile, which they join
    (`DeviceProfile.skus`, a grouping change; H617C stays first). Not hardware-verified: none is on the bench, and
    no H611A / H6143 / H6144 hard version has been read.
- **H70B3 / H70B4 / H70B5 / H6810 / H6811 / H8811**† (h70b3 229 / 230 / 231 / 255 / 256) **on an advertised
  pact only**, as straightfloorlamp 69. The app registers (1,1) for each of these goodsTypes too, and there the
  screen runs the polled connect read, not the 0xAC status read the rows serve: `Support.N` is false only at
  (1,1), so `VM4H70B345.d0` runs `r6` (`AfterConnectSuccessReadDeviceInfo`, which reads aa 06 and aa 07 among
  others). The rows leave (1,1) out, and now they are `pact_required` too: a pact-less record, which may be a
  (1,1) unit, is refused rather than given the status profile.
- **H70B6** (h70b6 329) **on an advertised pact only**, as straightfloorlamp 69. The app accepts (1,1), (1,2)
  and (1,3) for goodsType 329 (`Config4H70B6Manager.b:112-127`), and above (1,1) its status read adds TLVs
  0x7D and 0x7E, `ac 03 04 41 39 7d 7e` (`Support.d` / `e:57-75`, `VM4H70B6.i5:478-488`). The row serves (1,1)
  only, so it is now `pact_required` too: a pact-less record, which may be a (1,2) / (1,3) unit, is refused
  rather than polled with the (1,1) read `ac 03 02 41 39`. The app never runs the screen without a pact: it
  starts from the account's stored pact (`h70b6 Model.t:104-105`), takes each matched broadcast's pact block
  before it connects, and doesn't connect for a broadcast without one (`AbsViewMode4Op.onScanEvent:1960-1983`
  -> handler 999). Not hardware-verified (no H70B6 is on the bench).
- **H6198 / H6199** (pact_tvlightv2 24) **on an advertised pact only**, as H70B6 (the user's decision). The
  app accepts (1,1), (2,1), (3,1) and (4,1) for goodsType 24 (`Support.addSupportPact:57-74`). At (1,1) its
  colour write is `33 05 0b R G B [K] [mask]` (`Config.pu:15671-15674` gives 6 at pactType 1, so
  `Config.J[6]` = `Config.eu:13930-13934` -> `Bytes.c:239-267`), which no colour scheme serves, so the row
  lists (2,1)-(4,1) only. It is now `pact_required` too: a pact-less record, which may be a (1,1) unit, is
  refused rather than sent the `33 05 15 01` colour frames the app never sends one. The app never runs the
  screen without a pact: `VM4Light.X0:770-772` is true, so a broadcast without a pact block goes
  `AbsViewMode4Op.onScanEvent:1960-1983` -> `AbsViewMode4OpWithOtaCheck.j1:570-591` (null) -> handler 999, the
  old-app dialog (`AbsViewMode4OpWithOtaCheck.F1:271-283`), and the screen never connects to it. With them,
  H611A / H6143 / H6144 (above) and H615A-E (below) the pact-gated SKUs are 36: 35 by default and H6119 behind
  `allow_modelled` (H611A / H6143 / H6144 are gated by default only: `allow_modelled` also takes them without a
  pact). H6198 / H6199 are new since 1.6.1, so no released unit changes. Not hardware-verified (neither is on
  the bench).
- **Brightness on a SKU whose range the pact picks** (`DeviceProfile.brightness_by_pact`: stringlightv2 H6121
  / H6141 / H6142 / H6154 / H6195, H6170 / H6196 / H613G†). These route without a pact, but a `Device` set up
  from a pact-less record (`create_device` with no advertisement, or one without a Govee pact block) refuses
  every brightness command until `Device.ingest_advertisement` hears the pact, `turn_on(brightness=...)`
  included, which then sends nothing, not even the power-on. Power, colour and colour temperature work
  meanwhile. Documented in `brightness_by_pact` and `create_device`; no behaviour changed.
- **The other Glide panels**, on the fixed `h6061` scheme (Fixed, below): **H6066** (96, 25 panels), **H6067** (97,
  20), **H6065** (121, 20), **H606A** (163, 21) and **H6063** (228, 20) admitted by default (`app_traced`). They
  run H6061's screen and builders (cubelight `SubLib` keys, `Model.t` -> `CubeAcLightDetail` /
  `CubeVM4Light`; `Config.x` = 5 and `Config.I[gt][4]` for all five), so they get its power, brightness,
  whole-device RGB and colour temperature, and the `aa a5` read-back. The app sizes every mask and the read-back
  from the user's stored panel layout, which the device never reports, so each row's count is a full layout's: the
  layout editor's cap for the goodsType's shape (`Support.getShapeViewType` -> `Constants.o:400-425`, 25 / 20 / 20
  / 21; the editor shows no add point at the cap, `BasicShapeBusiness.x:989-991` / `x0:1017-1019`, and
  `SplicePreviewAc.d1` refuses one). The four are `app_traced` at a full layout, the user's decision D1 (a), which
  extends decision 9 (H6061, Changed); a smaller layout is exact only with the account's layout, which a later
  release reads (D10). H6067's count was 23, `Support.getBulbStringMaxNum(97)`, the read-back default with no
  layout stored, which the editor can't make. H6063's layout is not made in the editor but reported by the device
  (`GraphicalIdentificationActivity.m1:467-534`, at pairing and at the settings re-check), which accepts 1..21
  panels. Its row is `app_traced` at the 20 panels its colour page drives, the user's decision 27 (2026-10-22): the
  page's selection array is `Constants.o(16)` = 20 long (`CubeColorModeV2$initView$1$7.selectedShape:49-51`, via
  `ConsV1.getShapeSupportMaxCount` -> `CanvasLayout.getShapeMaxCount` of `Support.getShapeViewType(228)` = 16), so
  a 21st panel a report could add is not addressed, as in the app. It joins the H6061 / H6067 / H6065 profile
  (identical facts at 20; `DeviceProfile.skus`, a grouping change) and sends the frames it sent behind
  `allow_modelled`. Not hardware-verified.
- **Bench, 2026-10-21 (the Mac, reads only):** one H61C3 and two H6076s, polled through `create_device` and
  `update()` on the new rows. A guard on `GoveeConnection._send_frame` refused any write; none was attempted.
  - **H61C3** (rgbiclight 141, pact (1,1), plaintext, `allow_modelled`): aa 01, aa 04, aa 05 01, aa 40,
    aa a5 01-05, aa 06, aa 07 03 and aa 07 02, all answered: on, brightness 100, 15 segments, soft 3.03.23,
    hard 3.01.10. The aa 40 reply's section sum is 0, so the app reads the 15 pieces (5 groups of 3) the row
    has. On that reading the row is `app_traced` (below).
  - **H6076 x2** (straightfloorlamp 69, pact (2,1), encrypted): the handshake, the app's status read
    `ac 03 02 a5 a3`, then aa 06 / aa 07 03 / aa 07 02, all answered: on, brightness 90 and 100, 7 segments
    each, soft 1.04.05, hard 3.05.00. One unit's mode TLV is a short `15 01`. The row's own decode
    (`gradual_14`, where 0x15 isn't decoded) reads that burst in full; the H60A6 default decode gave an empty
    reply.
- **H7004** (h7004 goodsType 0), a grow light, admitted by default (`app_traced`) as a power-only row at any
  pact, following the user's decision (2026-10-22: "Ship power-only"); `VM4Light.X0` runs no pact check at
  goodsType 0. Its screen reads aa 01 and the device info, never aa 04 or aa 05
  (`ControllerStrategyConfig.k1`), and offers no brightness: `VM4Light.supportBrightness` is false for H7004,
  so the screen's default header hides its slider (`DelegateHeader.X`). So the row has no brightness, and the
  library's poll reads aa 01 alone. The red:blue ratio (`33 0d`, aa 0d), the timers and the `33 09` clock sync
  are not modelled. Not hardware-verified.
- **`DeviceProfile.light_nonzero`** (keyword-only, default False; the spec's `power_rule.light_nonzero`): a
  polled light whose screen reads the aa 01 reply as on for any non-zero byte, as the legacy
  `SwitchController.parseValidBytes` and the H70BX connect read (`AfterConnectSuccessReadDeviceInfo.w`) do. A
  devices.yaml row opts in with its wire `power.light_nonzero` (the connect read) or
  `power.light_heartbeat_nonzero` (the aa 01 heartbeat, where the connect read takes 1: H1168's
  `HeartControllerWithBattery`). As with a plug's `relay_on_mask`, the field is the rule the library's poll
  follows, the heartbeat's where the row has one. Every other light still reads on only for a byte of 1
  (`ControllerSwitch.Companion.c`), so no admitted SKU's decoded state changes. `parse.parse_light_power`
  takes the profile's `wire_profile` for the rule (default: on at 1). The field covers the poll only: on such
  a row an advertisement's switch byte still reads on only for 1 (`Device.advertised_on`), as the app's device
  list decodes it for every light (`AbsBaseBleModel.z` over `BleUtil.parseBleBroadOnOff`), so a byte of 2
  reads off in the advertisement and on in the poll.
- **`DeviceProfile.color_page`** (keyword-only, default None; the spec's clientRow `color_page`): the colour page
  the SKU's control screen shows a reported colour on. `"stock"` is the newdetail `DelegateColorV2`
  (`DelegateCurMode.createView:185-203`), which shows a report of one colour that is a Kelvin tint in range as that
  colour temperature (Changed, below); `"none"` is a screen with no colour page (H8015, H6071, H6075). The
  generated table sets it on every row with colour temperature whose mode decode reports a colour; a profile
  built by hand (None) reads its reports as the decode does, as before.
- **`DeviceProfile.segment_color_temp`** (keyword-only, default True; the spec's clientRow
  `segment_color_temp`): whether `Device.set_segment_color_temp` may send a colour temperature to a piece
  selection, as the app's Part-tab Kelvin item sends one (`DelegateColorV2Part.Z0` ->
  `LightColorCapabilityCenter.C2` -> `SupportPartColorTemConfig.b`; Changed, below), but false on the SKUs
  whose item widens every pick to whole zones (the field's docstring lists them). The generated table sets
  it from the row; a profile built by hand keeps 1.x's behaviour. Where it is false, `set_segment_color_temp`
  raises GoveeBleNotSupported before any I/O, and `set_color_temp` is the device's colour temperature. It
  doesn't govern `set_zone_color_temp`, which needs SEGMENTS, COLOR_TEMP and a zone with segments, as in 1.6.1.
  **For integrators:** SEGMENTS plus COLOR_TEMP in `capabilities` no longer means `set_segment_color_temp`
  works. Gate a per-segment colour-temperature control on `profile.segment_color_temp` as well. A per-zone
  one needs what `set_zone_color_temp` needs, SEGMENTS, COLOR_TEMP and a zone with segments; don't gate it
  on the field. The Home Assistant
  integration's segment entities take COLOR_TEMP from the two capabilities alone, so on the 42 released SKUs
  listed under Changed their colour temperature would raise GoveeBleNotSupported.
- **H1162 / H1163 / H1167 / H1168** (h1162 goodsTypes 87 / 137 / 182 / 186), the Music Sync boxes, admitted by
  default (`app_traced`) as power-only rows at pact (1,1), following the user's decision to match the
  standalone screen. The switch turns the box's music sync on or off: `33 01 <on>`, polled with aa 01 and kept
  alive with an aa 01 heartbeat (fixed 2 s). H1162 / H1163 / H1167 match `AdjustActivity` (`Model.t`, cloud
  gid <= 0), whose aa 01 reply is on at any non-zero byte (`SwitchController.parseValidBytes`, the rows' wire
  `power.light_nonzero`). H1168 has no standalone screen: its row is the header switch of
  `AcMusicFeastDetail4H1168` (`VM4MusicFeast.G4` -> `DeviceToggleController`; the screen class is set in
  base.apk's `assets/base2home_config.properties`). Its aa 01 connect read is on at 1 (`Compose4ReadAllInfo`),
  but the 2 s aa 01 heartbeat that replaces it is on at any non-zero byte (`VM4MusicFeast.onEventHeart`,
  `HeartControllerWithBattery.parseValidBytes`), and the library's poll follows the heartbeat (the row's wire
  `power.light_heartbeat_nonzero`). The Music Feast screen (group control, CLAUDE.md rule 5) is the documented
  other path for H1162 / H1163 / H1167. The feast frames (`33 60`, `aa 60`), music, the battery and H1168's
  `33 60 01` feast-effect toggle are not modelled. Not hardware-verified.
- **H70B1** (h70b1 goodsType 134) and **H70BC** (h70bx goodsType 216), splicing panels on the H70BX screen,
  admitted by default (`app_traced`) at pacts (1,1) and (2,1): power, brightness, RGB and colour temperature
  on the h6006 frames, one piece, and the aa 00 keep-alive. Power is read back by the polled aa 01 read, on at
  any non-zero byte as the screen's decoder reads it (`AfterConnectSuccessReadDeviceInfo.w`; the rows' wire
  `power.light_nonzero`). That decoder was all the rows waited for; every core frame was traced. The app's
  live `ee 30` power push is traced too (`power_live: [push_30]`), but the library doesn't read `ee 30` pushes
  yet. Splicing, the aa 39 direction read and scenes are not modelled. Not hardware-verified.
- **`DeviceProfile.segment_brightness_groups`** (keyword-only, default `()`): disjoint groups of segment
  indices. On a profile with groups, `Device.set_segment_brightness` sends the `33 05 15 02` mask over every
  segment of each group that holds a requested index, and folds the brightness into `state.segments` over that
  same set. That is what the colour strips of these screens do before the write, with the groups {0-2}, {3-6},
  {7-9} and {10-13}: `ColorMode14Rect4ReverseSelect.canClickBrightness`, `H605CColorMode.canClickBrightness`
  and the H6097 `ColorMode.canClickBrightness`, via `DelegateColorV2Part.U` -> `DelegateColorV2.Z0` ->
  `ColorViewModel.m0`. A devices.yaml row sets it with `segment_brightness_groups` (Spec, below). The new SKUs
  of pact_h605b 120 / 142 / 243 / 360 and pact_h6099 191 / 273 / 327 (H605C, H6602, H6604 / H8604, H2A40 /
  H2A41, H6099, H6097, H66A0 / H66A1 / R2A80 / R2A81) get it as they are admitted, and so do three released
  SKUs (Fixed, below). No other profile has groups, so every other segment-brightness frame is unchanged.
- **`DeviceProfile.device_info_reads`** (keyword-only, default True; the spec's status-row
  `device_info_reads`): whether `Device.update` sends the device-info reads, `aa 06` / `aa 07 03` /
  `aa 07 02`. The generated table sets it False on every status row at `none` (Changed, below): there the
  library sends none of them, nor the re-read of a version that a firmware-gated fact waits for, and the
  versions and device id come from each status reply's TLV 0x07 alone, as on the app's screen
  (`Compose4BaseInfoSingleRead.w`). True keeps 1.x's reads, so a profile built by hand reads as before.

- **The legacy 0x02 colour scheme, `legacy_02`, and 10 SKUs on it, admitted by default (`app_traced`)**:
  H6105 (h6105), H6107 (h6107), H6129 (h6129), H6185 (h6185), H7310 / H7312 / H7317 (h73101217), H7318
  (h7318) and H6165 / H6166 (h6165), all at goodsType 0 and any pact. Their colour screens run colour
  instruction version 4 (`ColorModeControllerConfig.c` -> `Config.F`: `Config.w[SKU]` = 4, else `Config.x[0]`
  = `Config.Hu` = 4 -> `Config.J[4]` = `Config.fu`), which `wire.build` now sends:
  - RGB as `33 05 02 R G B` (`Bytes.a`);
  - a pick that is one of the app's Kelvin tint colours in the SKU's range as `33 05 02 ff ff ff 01 <the
    colour>` (`KelvinConfig.i` -> `Bytes.o`). `fu` makes that test on the H7310 / H7312 / H7317 / H7318 /
    H6165 / H6166 screens too, which have no colour-temperature control, over their KelvinConfig version 0
    range [2000, 9000]. On those six the state keeps the picked colour (`rgb_color`, no `color_temp_kelvin`
    and no `last_color_temp_kelvin`), as the app does (`ColorViewModel.u0` keeps the colours, and
    `VM4Light.syncKelvin` has no Kelvin control to move): they have no `COLOR_TEMP`, and their
    `min_kelvin` / `max_kelvin` only pick the frame;
  - colour temperature (H6105 / H6107 / H6129 / H6185) as the Kelvin's tint in that form: the Kelvin slider
    sends `Constant.getTemColorByKelvin(K)[2]` through `fu` (`ColorViewModel.U`), so no Kelvin is on the wire;
  - no segment frame: `fu` ignores the position set.

  None is hardware-verified: no light on the bench runs colour version 4, and none may be sent a 0x02 frame.
  The `0x0d` white-slot colour-temperature frame (`Config.GM` -> `Bytes.A`) is not part of the scheme: it comes
  only from the illumination tab (`View4IlluminationMode` -> `configKelvinController`) and same-model group
  control, and none of these SKUs' bundled mode lists has the illumination tab. H6113 / H6181 and the other
  goodsType-0 rows on colour version 4 stay held for their other gaps (routing below the gate, the
  `rgb_flag_tint` read-back, the GATT service, firmware gates, chip-gated brightness).
- **The h60a6 colour-temperature white slot** (`DeviceProfile.white_type`, devices.yaml `white_type`). The
  h60a6 frame `33 05 15 01 <slot> <K> <tint> <mask>` now carries the slot of the SKU's white type, the value its
  colour page hands `Bytes.r` (the app's ColorTemperatureType, through `LightColorCapabilityCenter.d1` ->
  `Config.H`): `rgb_white` = `ff ff ff` (the default, every SKU before), `rgb_black` = `00 00 00`, and
  `rgb_self_tem_color` = `00 00 00` over every piece and, over some, the tint as the colour with Kelvin and tint
  zero (the plain RGB bytes of the tint). It applies to every colour-temperature frame an h60a6 profile sends:
  `set_color_temp`, `turn_on(kelvin=)`, an RGB pick that is a Kelvin tint colour, and the segment and zone Kelvin
  writes where the screen has them; the builders `color_temp`, `segment_color_temp`, `segment_rgb` and
  `color_rgb_frames` take `white_type=`. Only an h60a6 profile may set another value. It admits **H61E5** (h61d3
  239) and **H61E6** (246) by default (`app_traced`), not hardware-verified: the ColorMode colour page
  (`ColorModeControllerConfig.c` -> `Config.Yt` -> `Bytes.r`) with `Config.P` = 3. Their Kelvin pick is
  whole-strip (`ColorMode.canClickColorTem`, `H61D3Support.i0`), so their segment Kelvin is refused (below) and
  only an RGB pick of a tint colour on some segments takes the partial form. Their writes take the live piece
  count, as the app's posSet does (Fixed), so a tint-colour pick over exactly the live pieces is whole in both.
  One colour-page difference is named, the user's decision D2 (a) (2026-10-22): the app refuses segment
  brightness while a selected piece shows a Kelvin tint colour (`ColorMode.canClickBrightness`), which the
  library doesn't gate, as on the admitted straightfloorlamp 69 / 377, dreamcolorlightv1 193, h7078 245 and h6047
  119 rows, whose strips refuse it on a Kelvin tint too. The gate is a later release (D2 (b)). Their
  illumination tab's Kelvin (`Bytes.C`, `ff ff ff` for them) is a second path and not modelled.
- **Whole-device-only colour temperature** (`DeviceProfile.whole_device_kelvin`, devices.yaml
  `color_temp_control: whole_device`). Where the screen has no Kelvin on part of the device,
  `set_segment_color_temp` and `set_zone_color_temp` raise GoveeBleNotSupported, as a masked Kelvin is a frame the
  app never sends. It is set by `whole_device` (a colour page whose Kelvin pick always sets every piece: H61E5 /
  H61E6) and by `illumination` (below), whose tab writes every piece. Such a row with segments says
  `segment_color_temp: false` too, so `DeviceProfile.segment_color_temp`, the field integrators gate a
  per-segment Kelvin on, is False there as well; no such table profile has zones.
- **Colour temperature from the illumination tab** (`DeviceProfile.kelvin_with_brightness`, devices.yaml
  `color_temp_control: illumination`). Where a screen sets colour temperature on its illumination tab alone,
  the app writes the Kelvin frame and then `33 04` <the brightness it holds> as one write
  (`View4IlluminationMode.I0`, one `Compose4DefWrite`). `set_color_temp` now sends that pair, each frame acked
  in order, and `turn_on(kelvin=)` sends the Kelvin frame before `33 04` (the given brightness, else the held
  one). An unknown brightness is read first with the profile's own read: the status read (whose reply holds
  100 when it carries no TLV 0x04, as the app's `DeviceBaseInfoFromDevice` does) or aa 04. If no reply comes,
  GoveeBleError and nothing is written (never a guessed `33 04`). On such a profile the pair and
  every `33 04` writer (`set_brightness`, `turn_on`) hold one Device lock from build to write, so a
  `set_color_temp` started while a `set_brightness` is in flight re-sends the new level, as the app re-sends the
  level its screen holds, not the old one; the pair's state takes the level it sent. The tab writes every piece,
  so the profile is `whole_device_kelvin` (the segment and zone Kelvin setters raise). Promoted from
  `modelled` to `app_traced`, admitted by default, not hardware-verified: **H60A4 / H80A4** (h60ax 302:
  `Bytes.C(K, 11, 1)`, the h60a6 frame over every piece) and, on the user's sign-off, **H8015** (bulblightv3
  328: `Config.N[2]` = `Bytes.A`, the h6188 frame), behind `allow_modelled` since 1.6.1, whose bytes change
  (Changed).
- **A polled row's aa 05 decode** (`DeviceProfile.mode_decode`): the aa 05 mode report is read with the row's
  wire `mode` (its music layout and empty-value rule), where the resolver refused any but the default on a
  polled row. Empty by default, so every other profile reads its reports as before (the same cached
  `govee_profile`). Only a music report decodes differently, and it folds to the same state. It serves
  **H60A1 / H80A1** (h60ax 235): h60a1 `Info4Detail.Q` (music v2_default), the illumination tab's Kelvin +
  brightness pair, 14 pieces, admitted by default with the app's numeric Kelvin compare (below). Not
  hardware-verified.
- **H60A1 / H80A1 (h60ax 235) are admitted by default (`app_traced`), with the app's numeric hardware-version
  compare for their Kelvin range.** New `DeviceProfile.kelvin_version_compare` (keyword-only, default
  `"string"`; in devices.yaml, a Kelvin `by_firmware` item's `compare`). At `"numeric"`, `kelvin_by_hardware` /
  `kelvin_by_software` compare the device's version with an entry's as `BleUtils.isHigherVersion` does: each as
  one int of its digits with the dots removed, 0 when empty or unparsable (`BleUtils.q0:1065-1074`), the
  device's at least the entry's (`Q:327-335`; inclusive, as `isHigherVersion$default:74-79` passes it). So
  `1.4.3` (143) and an unparsable version are below `1.04.03` (10403), where a string compare puts them above.
  - **Row 235:** that is the compare `KelvinConfig.Config.Y2:1434-1436` makes for goodsType 235 (`:423-427`):
    [2700, 6500] (version 2, `:407`) from hardware 1.04.03, else [2200, 6500] (version 1, `:406`). The row's
    base range is now [2200, 6500], with a [2700, 6500] branch from 1.04.03. Below that hardware the illumination
    tab offers 2200-2600 K, and an RGB pick of one of those ten tint colours goes out as the colour-temperature
    frame (`Bytes.r`), as the app's `KelvinConfig.i` sends it. Until `aa 07 03` gives the hardware version, the
    range is [2700, 6500], the one valid on every revision.
  - **Every other profile** keeps `"string"`, the compare of `KelvinConfig.Config.z2` / `Z2` / `X2`
    (`compareTo`), and resolves its Kelvin range exactly as before at every version (checked against 535f63e
    on 22 versions, among them `1.4.3`, `10.00.00`, an empty and an unparsable one).
  - Not hardware-verified: no H60A1 is on the bench, and the read-only `aa 07 03` check (decision D13) was not
    run.
- **The colour read-back of H61A9 (dreamcolorlightv1 204) and H61C2 / H61C3 / H61C5 (rgbiclight 141) is sized as
  the app sizes it, and H61A9 is admitted by default (`app_traced`; listed above), no longer behind
  `allow_modelled`.** `VM4Light.N5` reads `N / g + (N % g != 0)` `aa a5` groups (Java division, none below 1) and
  keeps the first N pieces, N being the aa 40 reply's two section bytes summed as Java bytes (`ControllerIcNum.d`
  -> `ControllerStrategyConfig.R1`), or the static count (15 / 18) when the sum is 0. Where that reader throws (an
  IC field of 0x1000 or more) the app walks nothing, and now neither does the library, nor when the aa 40 read
  goes unanswered (it has no count to size the walk with). New
  `DeviceProfile.color_readback_ic_sections` (keyword-only, default False; devices.yaml `ic_pieces: sections`). The
  aa 40 read keeps its place in the poll. The frames are the ones sent before for a section sum of 0 or 13-15 on
  141 (groups of 3, 15 static; the bench H61C3 reads 0) and of 0 or 17-20 on 204 (groups of 4, 18 static).
  Rgbiclight 141's promotion on the H61C3 bench reading (the user's decision 1, Spec below) stands; this makes
  its connect walk exact on every unit. Source-modelled: no H61A9, H61C2 or H61C5 is on the bench, and no non-zero
  sum has been read. The live-IC rows (H6641 and the other h61d3 rows) keep their walk.
- **H7061 (h7050 98), a two-head flood light, admitted by default (`app_traced`).** Its aa 05 0x15 report is two
  `[bri s8][r g b]` head records (`bri_rgb_pair_15`), which the library decoded but did not fold. It now folds
  them as the colour page holds them (`H70606162Info4Detail.I`; `H7061ColorMode.updateColor` /
  `updateBrightness`): head 1 / head 2 to segments 0 / 1 with their brightness (clamped to 0..100), the
  whole-device colour only while both heads show the same one, and a shared in-range Kelvin tint as that colour
  temperature (`ColorViewModel.initColor4Device` -> `KelvinConfig.f`, as an RGB tint pick is folded on the write
  side). No Kelvin is read from the report itself (its default parse's `s16be(r1, g1)` is head 1's red and green).
  New `parse.HeadPair` in `parse.ModeColor`. Every frame is the h60a6 one the row already resolved to. Source-modelled,
  not live-verified: no H7061 on the bench, and what the heads report after a CCT write is unread.
- **H6057 (h6057 104), admitted by default (`app_traced`): the HA power is its light switch, `33 30`, read back
  from the aa 34 light flag** (the user's decision, 2026-10-22: "Light switch 33 30"). H6057's screen never
  reads aa 01: its state read is the aa 34 device-detail reply (`Controllers.f` / `e`, `H6057DetailVM.v5`).
  - **The read:** new `power_read: aa34_detail` (`DeviceProfile.detail_power_read`, keyword-only, default
    False). The poll reads aa 04, then aa 34 in place of aa 01, then aa 05, the screen's connect order
    (`Compose4InfoBleIotH6057`), and takes the light-open flag, byte 0 bit 7 (`Controllers.d`,
    `H6057Info4Detail.H`): the screen's on / off, and the light switch's state. With white noise playing and
    the light closed it reads off, as the screen shows it. New `build.device_detail_query` and
    `parse.parse_detail_power`; the frame describer (`wire.describe`) reads the aa 34 reply as `wire.parse`
    does.
  - **The write:** new `power_write: light_switch` (`DeviceProfile.light_switch_power`, keyword-only, default
    False) and `build.light_switch`. `set_power`, `turn_on` and `turn_off` write the header's light switch,
    `33 30 01` / `33 30 00` (`H6057DelegateHeader.s0` -> `H6057DetailVM.h5`:
    `ControllerSingle.c(BleProtocol.command_light_switch, [1 / 0])`), in place of the `33 01` main switch, so
    the power and its read-back are the same switch in every state.
  - **No advertised on/off:** the aa 34 read is the power's only read-back. `Device.advertised_on` is None on
    this profile, so `ingest_advertisement` folds no on/off into `is_on` (it still learns the pact and the
    encrypt flag). The device-list card reads the advert's switch byte (`AbsBaseBleModel.z`) and its toggle
    writes the `33 01` main switch (`AbsLightModel4BleIot.v0` -> `AbsDeviceModel4BleIot.l`); the control
    screen never reads it. The app doesn't settle which switch the byte carries (the screen also stores its
    light flag in the card's switch cache, `AbsAcLightDetail.Q2`), and if it is the main switch, white noise
    playing with the light closed would read on until the next poll.
  - **For integrators:** the light switch drives the light alone. `turn_off` closes the light and leaves the
    white-noise speaker playing, as the screen's light switch does; the library never sends the main switch
    (`33 01 00` would stop white noise too: `switchTrigger`, `D5`). White noise, its volume and the other
    speaker controls are not modelled.
  - The screen hides its colour and brightness controls while the light is closed (`ViewConnectSuccess.J`);
    the library sends them whatever the light flag, and `turn_on` (unless `power=False`) sends the light
    switch before them. Colour and colour temperature are the h6006 frames, Kelvin [2000, 9000], brightness
    `33 04` percent, the aa 00 keep-alive. The ee 30 pushes are not read. Source-traced (Govee Home 7.7.02), not
    live-verified: no H6057 on the bench and no firmware dump.
- **The empty secret check: `DeviceProfile.empty_secret_check`** (keyword-only, default False; devices.yaml
  `empty_secret_check`, only with `account_lock`) and `wire.build.empty_secret_check()`. Some screens send their
  `33 b2` account-lock check on every connect, with no secret test: hollowlamp `H6058NewDetailVm.T4:128-135`
  (first in the connect read, `:168`) and h6071 `VM4Light$afterConnectedSingleReadDeviceInfo$1$4$1.
  initControllerNoEventList:126` (first at `:184`) take `Controller4Secret.Companion.a` of the account's
  `secretCode`, which `DefInfo:106-110` stores as `""` when the record has none. `Encode.decryByBase64("")` is no
  bytes, and `BleUtils.p:1030-1041` zero-fills the frame, so with no secret the check is `33 b2`, 17 zero bytes
  and the xor (`govee_ble_frame` `secret_write` with its 8 bytes zero, Spec). On such a profile a `Device` with no
  secret set now sends that empty check after the handshake on every session, where it sent no unlock; with a
  secret it sends the secret's check, as before. `Device.read_secret()` is the pairing screen's read, which sends
  the `aa b1` read alone while the account has no secret (`AbsPairAc4SecretV1.T1:107-113`): a session it sets up
  sends no empty check, and the next other operation on that session sends it first. That holds for operations
  that overlap the read (`asyncio.gather`) too: one waits for the read and then sends the check before its own
  frames, and a session one sets up while the read is in flight sends the check at setup. A device that refuses
  the empty check (a non-zero status) fails the session with GoveeBleAuthError, as the app's connect read fails
  on it (`AbsControllerNoEvent4Single.onResult:229-230` -> `AbsComposeControllerWithOpResult.onResult:330-334`),
  at session setup and when the next operation after a `read_secret()` sends it: either way the session ends,
  so no later frame goes out on that link unchecked and the next setup sends the check again (source-modelled).
  An operation that overlaps that check waits for its outcome (Fixed, below). A missing ack is logged and the
  session goes on. Screens that test the secret first send none
  (`H605051NewDetailVm:163-169`: H6050 / H6051), and nor do the plugs. Set on **H6058** (hollowlamp 59), now
  admitted by default (`app_traced`; it was behind `allow_modelled` for this one gap, the user's decision D4,
  2026-10-22), and on H6071 / H6075 (h6071 57 / 67, Changed). Source-modelled, not live-verified: none of the
  three is on the bench, and whether their firmware needs the empty check is untraced.
- **A write-only row type: the app's device-list card** (`DeviceProfile.session`, `"held"` by default, or
  `"per_command"`; `GoveeConnection.send_once`). It is for SKUs whose control screen has no BLE control, where
  the user chose to follow the card (DECISIONS.md 6). On a `per_command` profile:
  - each write is a session of its own: connect (the BGC read and the handshake as on every connect), write the
    one frame, read the reply for at most the write-ack timeout, disconnect. A refusal (a non-zero status)
    raises `GoveeBleCommandRejected` and applies nothing; no reply counts as success, as on the card, and
    nothing is retried or sent twice. Each session starts at least 1.1 s (`const.PER_COMMAND_SESSION_GAP`,
    `CardCommManagerProxy.s`) after the last one's release, failed or not;
  - `turn_on(power=True, brightness=x)` is two sessions, power first, and a brightness while the device is not
    known to be on is sent after a power-on session (the card's switch, then its slider), with no other write
    of the `Device` between the two. Each applies its own state once its write completes, so a completed
    power-on stays when the brightness fails. Colour is refused;
  - `update()` and `connect()` make no connection, and `read_secret()` raises `GoveeBleNotSupported`: the card
    reads nothing over GATT. On/off is the advertisement's switch byte and the last completed write (acked, or
    unanswered, as on the card); brightness is the last sent, and the state stays `optimistic`;
  - no heartbeat, idle timer or warm window: no link outlives its write;
  - a `DeviceProfile(session="per_command")` takes POWER and BRIGHTNESS only and no zones
    (`GoveeBleInvalidValue` otherwise): a scene upload or a bar's read-modify-write would hold a link past its
    session.

  Every `held` path is unchanged by construction: `_commit`, `turn_on`, `set_brightness`, `update`,
  `connect` and `_read_reply` dispatch a `per_command` profile to its own branch first. Not live-verified: no
  unit is on the bench.
- **H70B2** (h70b2 185, pact (1,1)) and **H6630 / H8630** (h6630 285) and **H6631** (h6630 286), admitted by
  default (`app_traced`) on that row type: power and brightness (1-100), the card's `33 01` and `33 04`. Their
  control screens have no BLE control (185's pact check refuses every scan hit; 285 / 286 send every command
  over IoT), so the rows follow the card, as the user decided
  (DECISIONS.md 6, brightness kept on 285 / 286). Not live-verified: there is no unit and no firmware image.
  Open, and named in each row's `doc`: whether the advert's switch byte carries the on/off on these firmwares
  (the app's IoT poll, which corrects it, has no library counterpart, so the state is assumed), the BGC
  version (an AES-GCM unit gets `GoveeBleNotSupported` on every command), and whether a write needs the
  `33 b2` unlock (a refusal raises `GoveeBleCommandRejected`). The row type changes no other SKU (Changed).
  - **Home Assistant, release order** (the user's decision, DECISIONS.md 16: "Integration support first"):
    - first, an integration release that handles these rows, keyed on `profile.readback == "none"` (power and
      brightness as two calls, assumed state, availability from the advertisement). It ships dormant on its
      current library floor: no library released before this one has a `readback "none"` profile, and it reads
      the attributes new here (`session`, `whole_device_kelvin`, `supports_segment_brightness`) with
      fallbacks, so it runs on those libraries unchanged;
    - then the library release that admits H70B2, H6630, H8630 and H6631 (this one);
    - then an integration release that raises its floor to that library release.

    The order closes a window: an integration without that handling also accepts this release (`>=1.2.1,<2`)
    and gets the four SKUs, but sends a power-on with every brightness change (their state stays
    `optimistic`), so its one `turn_on(power=True, brightness=x)` call is two sessions inside its 60 s command
    timeout, and a slow link can time out the brightness after the power-on landed and report the command
    failed.
- **A status read per advertised pact** (`DeviceProfile.status_read_by_pact`, keyword-only, default empty): where
  a row's pact branch asks for other TLV types than the row's read without its colour groups, that branch is the
  pact's exact read, `((pact_type, pact_code), types)`, which `Device` requests at that advertised pact; the
  row's own `status_read` applies at any other pact and before one is heard (the spec's `client_row`). The
  colour-group gate (`status_color_groups_pact`, H60A6's) still applies to the read in force, and stays the only
  form a SKU of the 1.x pact fallback may take: there a pact no row lists routes the SKU to the row, and the gate
  decides its read, as in 1.x. The forms check and the colour-group decode serve every read at any pact. It
  admits **H7070** (h6092 283) by default (`app_traced`), on the H7070 screen with H7071 / H7073: power,
  brightness and the aa 00 keep-alive, with the read `ac 03 02 41 0c` at pact (1,1) and `ac 03 03 41 0c 42` at
  (1,2) / (2,1), where `Support.supportEffectChange` adds the effect-change switch (`VM4LightH7070.T4`). As on
  H7073, the screen reads TLV 0x42 byte 0 and TLV 0x0c's operate layout unguarded, where the library, with no
  form for either, still applies the burst's power and brightness. Not hardware-verified.
- **Colour instruction 9, the `kelvin_v9` colour scheme, and H6071 / H6075.** `33 05 15 04 <K u16be>`, a
  Kelvin and nothing else (`Config.N[9]` = `Config.JM` -> `Bytes.D`, the spec's `op15_group` form
  `kelvin_v9`): `wire.build.color_temp` builds it, and every RGB and segment builder refuses the scheme with
  GoveeBleNotSupported (the colour page's `Config.J[9]` = `hu` maps a pick to its Kelvin, so there is no RGB
  frame, and there is no mask). `Config.x` gives version 9 to goodsTypes 57 and 67 only. It admits **H6071**
  (h6071 57) and **H6075** (h6071 67) by default (`app_traced`), one profile: power, brightness (percent) and
  colour temperature [2200, 6500], polled with aa 01 / aa 04 / aa 05 (`rgb_kelvin_15`), the aa 01 keep-alive,
  and the `33 b2` unlock their screen sends first (`account_lock`: with the account's secret set, after every
  handshake; with none, the empty check, Changed). Their bundled mode list is
  `[10, 13]` (base.apk `localSkuComponentConfigs.json`, `ModeConfig.e`): scene and illumination, no colour page,
  so colour temperature is the illumination tab's pair (`View4IlluminationMode.I0`): the `15 04 <K>` frame, then
  `33 04` <the held brightness> (`color_temp_control: illumination`, `kelvin_with_brightness`). They keep their
  traced `color_functions: [segment_brightness, color_temperature]`: with no relative-brightness version the
  slider sends nothing, and `kelvin_v9` has no segment-brightness frame, so the flag calls for no `segments`
  (the user's decision 4). Not modelled: the `33 09` clock sync and the timer, wake-up and sleep reads. Not
  hardware-verified: neither is on the bench, and the aa 05 echo after a `15 04` write is unread.
- **A pairing write's pact is an exact-pact condition.** An `assigned_goods_type` item of the pairing flow
  (`context: bind`) writes its goodsType into the record only when the advertised pact is exactly its `pact`
  (`NewRgb2TLUtil.isNewRgb2TL` / `isNewRgb2TL4BleIot`: H615A-E -> 40 and H613A-F -> 39, at (65535, 255)), so it
  no longer refuses its SKUs at every pact: the build refuses only a row of another goodsType that lists such a
  SKU and accepts that pact (or `any`), and the generator lists each (SKU, pact) route no row carries for review
  (`_resolve.bind_review`: the eleven at (65535, 255)). A pairing item without a pact is refused on a routed SKU
  as before. It admits **H615A / H615B / H615C / H615D** (stringlightv2 40) and **H615E** (108) by default
  (`app_traced`), on an advertised pact (2,1) only, the one their rows list: the rgblight control screen as
  stringlightv2 1 (`Model4BleIot` -> `/rgb/newdetail`; the uiv3 reads, `Support4StringLightV2.L`), percent
  brightness, the h6006 frames (`Config.x[40]` / `[108]` = 2 off the NewRgb2TL pact), [2000, 9000] and the
  aa 01 keep-alive. A unit advertising (65535, 255), where the app runs colour version 4 (the `legacy_02`
  frames), raw brightness [6, 254] (`Support4StringLightV2.b`) and the pairing write's goodsType, is refused:
  no row lists that pact. So is a unit whose advert carries no pact, which may be such a unit: each route of a
  SKU a pairing write names needs an advertised pact (`_resolve.Route.needs_pact`, as on the BK rows' target
  routes, above). As for those, `supported_skus()` lists H615A-E, but `is_supported_sku(sku)`, `profile_for`,
  `make_device` and `create_device` refuse one without a pact: pass the advert's. Not hardware-verified.
- **A later advert's pact re-selects the row** (`devices.profile.reroute`, which `Device` follows on every
  advertisement it reads, and at construction). Where a pact heard after the Device was built routes its SKU to
  another row whose profile differs from the held one only in what a pact picks within a row (the status read,
  the scene set, the Kelvin and brightness ranges, the SKU list), `Device.profile` becomes that row's profile,
  as the app's screen runs the row the unit's pact gives it. The capabilities, zones, segments, cipher fallback,
  keep-alive, unlock and connect writes are equal by construction, so the link and a caller's entities stay
  valid; the callbacks fire as for any pact change. It serves the BK route of **H6118 / H6194** (carlightv1)
  and **H6179** (tvlightv1): built from an advert without a pact they take goodsType 6 / 12's raw brightness
  [20, 254], and an advert at (10, 1) / (10, 2) now moves them to goodsType 39's percent brightness, as the
  app's rewrite does (`OldRgbBkUtil.isOldRgbBk`, `Model.itemClickOp` / `ModelBle.itemClickOp`); a later
  (1, x) moves them back. Any other change of row (a pact the app's scan refuses, or another row's
  capabilities, as H60A6's (1,1) pact route, Fixed) keeps the profile and logs the warning as before. The
  support check already takes the advertised pact (`is_supported_sku(sku, pact=)`, `scanner.supported`,
  `create_device`, `make_device`). Source-modelled, not live-verified: none of the three is on the bench.
- **Segment brightness follows the colour page** (`DeviceProfile.segment_brightness`, keyword-only, default
  True; `Device.supports_segment_brightness`). A row with segments offers the `33 05 15 02` write only where
  its colour scheme has the frame (h60a6) and its colour page the slider: a traced `color_functions` without
  `segment_brightness` turns it off, and `set_segment_brightness` then raises GoveeBleNotSupported before any
  I/O; an untraced `color_functions` keeps the 1.x rule (on). Segment colour and the segment Kelvin rules are
  unchanged. It promotes **H61D2 / H61D3 / H61D4 / H61D5 / H61D6** (h61d3 202) from `modelled` to `app_traced`,
  admitted by default and now with segments: the screen's colour pick masks the selected pieces
  (`ColorModeH61d3.canClickColor`; `ColorViewModel.p0` -> `ColorModeControllerConfig.c`, one h60a6 frame over
  the selected of 14 pieces, a Kelvin tint colour as the partial `Bytes.r`), its Kelvin pick sets every piece (`canClickColorTem`: `color_temp_control: whole_device`, so the
  segment and zone Kelvin setters raise), and its segment-brightness slider is hidden (`Config.I[202][3]`
  false, `DelegateColorV2Part.Z0`). Not hardware-verified.
- **The live IC count's piece rules** (`ic_pieces` object forms, read from the aa 40 reply on the direct colour
  read-back). Besides the clamp and divisor, which rounded up (H6641: `H61D3Support.e`), the walk now follows:
  - **floor**, Java's integer division (`DeviceProfile.color_readback_ic_rounding`, keyword-only, default
    `"ceil"`): h705a `Support.b` gives goodsTypes 125 / 240 `IC / 4` pieces;
  - **an IC table** (`color_readback_ic_table` with `color_readback_ic_otherwise`, keyword-only, default empty /
    None): h6609 `Support.getColorPieceSize(173, IC)` gives 18 pieces at IC 36, else 14;
  - **two strips** (`color_readback_ic_pair_cap` with `color_readback_ic_otherwise`, keyword-only, default
    None): where the IC-pair reader (`ControllerIcNum.e`) returns a second pair, the first IC is not 0
    (`VM4Light.h5` keeps no other) and the second is above 0, `min(IC 1, 15) + min(IC 2, 15)`, else 30
    (`H61D3Support.e` for goodsType 375, H1A45); none where that reader throws.

  `parse.IcCount` gains `ic_pair` (the counts that reader returns). `_readback.live_ic_pieces` now takes the
  parsed reply and returns None where the app's reader stores no count. Every profile that had a live IC
  count before keeps its rule (rounding up, no table, no pair), so its reads are unchanged; the h705a rows are
  under Changed. H6609 and H1A45 take the table and the pair, with the live write count (below).
- **The live piece count sizes the colour writes on every IC-sized row** (`DeviceProfile.color_writes_live_ic`,
  keyword-only, default False; every devices.yaml row with an `ic_pieces` object form sets it, and it needs
  `color_readback_live_ic`). These screens take their colour strip, and so every pick's position set and the
  whole-device mask, from the stored IC, which the aa 40 read updates, with no cap but their rule's
  (`ColorPieceConfig.c`), where the library wrote the row's static count. The h705a screens resize a built
  strip when a read changes the count (`ColorMode.Y`, `ColorModeV1.K`); the h61d3 and H7067 screens store it
  without resizing (`VM4Light.k5` / `h5` / `Q4`), so in the first app session after the IC changed with no app
  connected their strip keeps the old count where the library takes the new one, a divergence each row's doc
  names. `Device.segment_count` is now each poll's live pieces (the poll's
  one aa 40 read, in its place before the aa a5 groups; at most 112, `build.MAX_MASK_SEGMENTS`, the most
  positions any body carries, so every larger count writes the same bytes): the whole-device mask and the
  segment index bound. Before the first poll a write that carries a mask reads aa 40 first, and raises
  GoveeBleError, sending nothing, when the device gives no usable count (no reply, an IC of 0x1000 or more, or
  no pieces), as H6062's aa 41 does. `DeviceProfile.segments` is then only the count before that read.
  The h705a screens read the aa 40 reply with the IC-pair reader (`ControllerIcNum.b` -> `e`), which stores no
  count where either IC field is 0x1000 or more (`DeviceProfile.color_readback_ic_pair_reader`, keyword-only,
  default False; the rows' wire `f_40: ic_pair`), and H1A45's pair rule needs it.
  - Promoted to the default table (`app_traced`), the write mask having been their one modelled fact: h705a
    **H705A / H705B / H705C / H801B** (125), **H705D / H705E / H705F / H805A / H805B / H805C / H805D** (240)
    and **H7067 / H7068 / H7069** (250); h61d3 **H61B8 / H61B9** (299) and **H61F2 / H61F5 / H61F6** (261),
    behind `allow_modelled` since 1.5.0.
  - **H608A / H608B / H608C / H608D / H808A** (h705a 184) take the live count too and are admitted by default
    (`app_traced`), with one divergence the user approved (decision 21(a), "Live count only"). The app sizes its
    first aa a5 walk from the server's per-SKU IC, not the account's: `Model.t` passes the account's IC, but
    `AcNewDetail4H608A.e3:146-206` drops it, so `AbsAcLightDetail.K1:594-596` takes `SkuIcManager.d:118-124`
    (`GET /appsku/v1/skus`) and `VM4H608A.a5:252-254` walks that. The library walks this poll's aa 40 count. The
    app then settles on the live count itself: `VM4H608A.S4:516-538` sets it and uploads it with
    `updateDeviceIc`, so only a connect's first walk differs, and only where the device's IC differs from the
    per-SKU one. An IC of 0 is no count there (`DeviceProfile.color_readback_ic_skip_zero`, keyword-only,
    default False): `VM4H608A.S4` skips it and keeps the count the screen holds, so the library keeps the count
    it has (and raises before its first one) where it would have written a one-piece mask. Not live-verified:
    no 184 SKU is on the bench.
  - **H1A45** (h61d3 375), admitted by default (`app_traced`): its two strips' rule (above) sizes the walk and
    the strip (`ColorModeH1A45.d0`, one position set over both strips, per-position colour, brightness and
    colour temperature), and its colour config is keyed by the named constant `LightRecycleViewGesture.W`
    (h60a6 frames, Kelvin [1000, 10000]).
  - **H6609** (h6609 173), behind `allow_modelled`, on its IC table: the app's IC there is the account's
    stored one (`Info4BleIotDevice.q()`, which the connect aa 40 never updates), for which the library has to
    take the connect read; and segments are withheld, because its strips widen a
    segment-brightness selection to whole groups, which the library's `15 02` doesn't. Power, brightness, RGB,
    colour temperature and the gradual flag are served, over 18 or 14 pieces.

  None is hardware-verified: none of these SKUs is on the bench.
- **`DeviceProfile.special_info_read`** (keyword-only, default False; a devices.yaml row's `connect_reads`
  entry `special_info`): the device reports its own segment count in the 0xAC special-info read `ac a1`,
  whose multi-packet reply, under sub `0x0B`, is `[segments][IC per segment ...]` (the goodsType 180 screen's
  connect read: `ControllerReadSpecialInfoMultiBack.Companion.k`, `VM4Light.x5` -> `Info4Detail.z0`). That count
  sizes every mask and the `aa a5` walk, as the H6062's `aa 41` section count does: `update()` reads it before
  `aa 05`, a mask write reads it first while it is unknown and sends nothing without it (GoveeBleError naming
  `ac a1`), and the walk is skipped until it is known. New: `build.special_info_query()`,
  `reassemble.parse_segment_ic()` and `reassemble.read_segment_ic()`, `GoveeConnection.query`'s keyword-only
  `burst_command` / `burst_sub` and `reassemble.burst_chunks`'s keyword-only `command` / `sub` (defaults: the
  status read's, so every status burst assembles as before). `govee-ble-analyze` (`describe`) judges a burst
  by the reader its first chunk names: it labels the request `status-request/special_info` and counts a
  complete reply on its own line (the new `StatusBurstReport.special_info`), not as a malformed status burst,
  with an empty one a gap (the app's read fails on it). No admitted row uses it. Source-modelled, not
  live-verified: no goodsType 180 unit is on the bench.
- **`DeviceProfile.kelvin_from_pieces`** (keyword-only, default False; a devices.yaml row's `kelvin_readback:
  pieces`): the colour-temperature state comes from the `aa a5` walk's pieces, as the goodsType 180 screen
  takes it after its connect read (`VM4Light.g5` -> `ColorUtils.r` -> `SubMode4Color.s`): once a poll's walk
  has read every piece and the poll's `aa 05` report showed the colour mode, `state.color_temp_kelvin` is the
  Kelvin of their one colour when every piece shows the same one and it is one of the app's 178 tint colours
  (the whole 1000-10000 K table, not the row's range), else None, in place of the report's Kelvin. In another
  mode (a scene, music, DIY) the screen shows no colour temperature, so only `state.last_color_temp_kelvin`
  takes the pieces' Kelvin, as g5 syncs a non-zero one whatever the mode (`syncKelvin` -> `Info4Detail.f`);
  when the pieces give none, the report's own Kelvin stays the held one (`Info4Detail.V`). A walk that lost a
  group, or an unanswered `aa 05`, changes neither. The resolver takes `kelvin_readback` only on a polled row
  whose walk is not IC-sized (the fold counts the static or device-reported pieces). No admitted row uses it.
- **H706A / H706B / H706C / H806A / H806C** (h705a 180), behind `allow_modelled` (`modelled`): power,
  brightness (1-100), RGB, colour temperature [2200, 6500] with the white-type-0 slot (`00 00 00`), segment
  colour, colour temperature and brightness over the device's own segment count (the `ac a1` read, above), and
  gradual. The read-back is `aa 01`, `aa 04`, `ac a1`, `aa 05`, the discarded `aa 40`, the `aa a5` walk with
  the Kelvin from its pieces, and `aa a3`. `modelled` for one gap, which the row's `doc` names: the app's colour
  page refuses a segment-brightness pick while a selected piece shows a Kelvin tint colour, and the library
  sends it. Not hardware-verified (none is on the bench). Their `DeviceProfile.segments` is 12, only the bound
  (the segment-settings page's most), as the H6062's 33 is: the unit's own count (the app's defaults are 6 on
  H706A / H806A, 9 on H706B, 12 on H706C / H806C) is `Device.segment_count` once a poll has read it, and a
  segment index at or past it raises. An integration that makes one entity per `profile.segments`
  (hass-govee-ble-local's segment lights, disabled by default) offers 12, those past the unit's count
  unusable, until it sizes them from `Device.segment_count`.
- **H703A / H703B (h61d3 263): RGB read-back from the status read's TLV 0xA5.** The screen reads that TLV as
  one `[brightness, R, G, B]` record at bytes 1-4, from a value of 5 bytes or more, never reads byte 0, and never
  fails the read on it (`VM4Light$afterConnectedSingleReadDeviceInfo$1.n`); `SubMode4Color.l` takes the record
  only at a piece count of 1, which 263 has (`H61D3Support.e`). The library skipped the TLV (its colour groups
  would fail the burst on group byte 0), so these SKUs had no colour read-back. It now reads it under the new
  wire form `f_a5: single_record` (Spec, below) into `reassemble.StatusReply.color` (the last such TLV wins;
  default None), and `_fold.apply_status` folds it as the whole-device colour before the same burst's TLV 0x05,
  so a Kelvin above 0 there still reads as colour temperature, a Kelvin of 0 keeps the colour, and a scene
  clears it. That order is a library choice: the app holds the Kelvin and the piece colour side by side, and
  `state` keeps one. The request is unchanged (`ac 03 04 41 a5 40 16`), and no other row reads the form, so no
  other SKU's decoded state changes. Source-modelled: no H703A / H703B is on the bench, and what the firmware
  puts in byte 0 is unread.
- **`DeviceProfile.heartbeat_power`** (keyword-only, default False) and **`DeviceProfile.heartbeat_reread_pieces`**
  (keyword-only, default 0), set by the new devices.yaml row facts `heartbeat_power: true` and `heartbeat_reread: {
  pieces }` (Spec, below). A status-read screen on the aa 01 keep-alive reads the 0xAC status once per connect
  (`afterConnectedSingleReadDeviceInfo`, from `d0`) and then sets its switch from byte 2 == 1 of every keep-alive
  reply (`BleComm.G` -> `ShareVM.N3` -> `J3`, ConnectHeartbeat type 1); on a change, four of the screens also
  re-read the mode and colour (`J3` -> `Compose4Off2On`). The user's decision 17 ("Match the app") turns it on for
  the rows it named, and the user's decision 26 (2026-10-22) for the h7020 rows on 296's screen admitted after it:
  316 / 318 / 337 / 391 / 411 (H702A-C, H7026 / H8026, H7027, H30D0 / H30D1, H3010-H3013), whose screen takes the
  switch from each reply with no re-read (`NewDetailVM.I3:735-742` true, `J3:744-756`), as H7025's does. h61d3
  263 has it too. The library now does the same on every status row on the aa 01 keep-alive; on the h7020 rows
  whose connect read also reads aa 40 around the status read (316 / 391 / 411), those reads go with it, once per
  link:
  - **The status read once per link.** `Device.update` makes it on the first poll of a link and reads nothing more
    on that link (so nothing is read back after a write there); a poll on a fresh link returns its status read's
    state.
  - **The keep-alive reply as a power read.** Each reply that arrives with no other frame sent since its beat sets
    `state.is_on` and reaches the state callbacks (`GoveeConnection(heartbeat_reply=...)` hands over every frame
    notified then, and `Device` reads the aa 01 reply out through the spec); a reply that may predate a write or a
    read is dropped, where the app takes every aa 01 notification. It follows #41's rules for
    `Device.power_read_at`: the reply stamps it, with the drop count taken when its beat went out (the new
    `GoveeConnection(heartbeat_sent=...)`, called once each beat is written), so a drop after the beat leaves it
    unstamped, and as a read it drops nothing; any other change to `is_on` still drops it.
  - **The power-change re-read** where the screen makes one (`heartbeat_reread_pieces` above 0): at once, on the
    link the reply came on and never another (the new `GoveeConnection.query(on_link=...)` sends nothing and
    connects not when that link has gone), with the beat held as a compose in flight holds the app's (`AbsBle.I`):
    the aa 05 mode read, folded as a poll's, then, once it is answered, the screen's aa a5 colour groups over that
    many pieces in groups of the row's `num_per_group` (`Controller4ColorInfoByGroup.generateReadController`, added
    by `Compose4Off2On.w`), ending at the first that goes unanswered as the compose ends
    (`AbsComposeControllerWithOpResult.onOvertime`; `_readback.walk_mechanism_a_direct(stop_at_miss=True)`). The
    colours are applied as segments where the profile has SEGMENTS over exactly those pieces, and read and not
    applied elsewhere. The compose's timer, sleep and wake reads are left out, as every connect read here leaves the
    setting reads out. A change that comes while a re-read runs sets the power and sends no re-read of its own,
    as the app skips a `Compose4Off2On` that comes while a compose is in flight
    (`AbsViewMode4Op.doCommController:1074` -> `ComposeYieldGuard.skipIncomingIfBusy:21-31` with `AbsBle.I`
    true, `Compose4Off2On.yieldToInFlightCompose:142-144`); `Device.stop` cancels a re-read still running.
  - **Rows** (Changed, below): straightfloorlamp 69 (H6076 / H8076 / R16D0: aa 05 then groups 1-2 over 7 pieces,
    applied), h6069 284 (H6069 / H8069: aa 05 then groups 1-35 over 140 pieces, read and not applied: a
    one-piece colour tab; 140 is the screen's count when no shape layout is stored, which the library has none of,
    where with a stored layout of n shapes the app sends groups 1 .. ceil(n / 4), `CubeVM4Light.l5`, an open
    question for the user), rgbiclight 351 / 352 (H617G / R617G, H618G / R618D: aa 05 then groups 1-4 over 15 pieces,
    applied), h61d3 263 (H703A / H703B, behind `allow_modelled`: aa 05 then group 1 over its one piece, not
    applied); and with no re-read, as their `J3` sets the switch alone, h7086 293 (H7086), h7020 296 (H7025 /
    H8025), h7076 313 (H7076) and h3700 383 (H3700). The fields are ignored on a zoned profile (the reply is the one
    switch).
  - A note that holds on every status row and sets no row's level (Spec, the h61d3 263 note): whether the firmware
    returns TLVs 0x01 / 0x04 / 0x05 unasked is not in the app; absent, the read gives off and brightness 100, in the
    app as in the library, and the keep-alive corrects the power only on a link that outlives a beat, through the
    state callbacks, never in the state a poll on a fresh link returns (Home Assistant's polls, with the idle
    disconnect, would read off each time). Source-traced (Govee Home 7.7.02), not live-verified: the H6076 bench run
    (2026-10-21) was read-only and saw neither the reply's switch nor the re-read.
- **A status row's pieces from its own IC read.** The 0xAC status screens that size their pieces by the IC take
  it from the status read's TLV 0x40 (H3401, H3510 / H3511, H7026 / H8026, H7027, H6671 / H6672: the screen's
  lambda stores each TLV 0x40's count, `Info4BleIotDevice.G0`, and reads it back at each 0xA5 TLV) or from aa 40
  reads around that read (H702A-C, H30D0, H3010-H3013: h7020 `afterConnectedSingleReadDeviceInfo4702ABC$1`
  sends aa 40 ahead of the 0xAC read, a mandatory step of the same connect read, and again once it succeeds,
  `NewDetailVM.s5`). On such a profile (`readback="status"` with `color_readback_live_ic`) the IC rule's pieces
  become `Device.segment_count` (the writes, at most `build.piece_cap`) and size the status read's colour
  groups (uncapped):
  - `reassemble.parse_status` takes `pieces_for_ic` and reads a burst again with the count of a TLV 0x40 that
    leads its 0xA5 TLVs; `StatusReply.ic_count` is the last TLV 0x40 count the walk reads, kept on a read that
    fails after it, as the lambda had stored it;
  - `DeviceProfile.ic_reads_around_status` (keyword-only, default `(False, False)`) makes the aa 40 reads; an
    unanswered or unusable first one ends the poll's status read, as it fails the app's connect read
    (`AbsComposeControllerWithOpResult.onOvertime`);
  - `DeviceProfile.color_readback_ic_raw` (keyword-only, default False) places the colour groups over the IC
    count itself where the colour page writes another rule's pieces (`Support.getColorPieceSize(gt, IC)` = IC):
    H3510 writes `ColorPieceConfig.b`'s 8 pieces at IC 8, else 4, and H30D0 15 at IC 15, else 30;
  - a colour write before the first count runs the status read (the TLV 0x40 rows) or the aa 40 read first,
    and raises GoveeBleError, sending nothing, without a count; segments past a smaller read-back count leave
    the state, as the screen's colour arrays take the new size (`SubMode4Color.j`);
  - `_readback.ic_rule_pieces` applies a profile's one-count rule to a count;
  - an IC count that gives no pieces (0, or below 0 on the signed readers) sizes nothing: the library keeps the
    counts it has and places the burst's colour groups over them. The app differs there (each row's `doc`):
    its strip takes the IC's 0 pieces (`SubMode4Color.j`), so a second TLV 0xA5 group, or below 0 the first,
    fails its read, which then applies nothing; on H3510 / H3511 it places no group (each lies past the IC / 4
    whole groups) and applies the rest.

  Promoted to the default table (`app_traced`), the live count having been their one modelled fact: h705a
  **H3401** (394: IC pieces, its whole-device RGB at IC 20 `33 05 15 01 ff 00 00 00 00 00 00 00 ff ff 0f`, where
  it wrote 56 positions, `ff` x 7) and h7057 **H3510**, **H3511**† (349: 4 pieces, or 8 at IC 8, the 2-byte mask
  as before; its read-back places only the groups whole within the IC count and drops later ones, failing
  nothing). Admitted by default, whole-device control (power, brightness, RGB, colour temperature; segments left
  out, as on H7025, the Part tab being account-sized): h7020 **H702A / H702B / H702C** (316), **H7026 / H8026**
  (318), **H7027** (337) and **H30D0**, **H30D1**† (391). For integrators: H3401 and H3510 / H3511 size their
  segments from `device.segment_count` after the first refresh, as the other IC-sized rows do. Source-traced,
  not live-verified: none of these SKUs is on the bench. Like every status row, the new rows also get the
  library's aa 06 / aa 07 device-info reads, which no firmware or bench evidence backs on them yet
  (status-row-versions-from-tlv07).
- **The `h3010` colour scheme: goodsType 411's colour write as one 0xA3 unit** (`ColorScheme` gains
  `"h3010"`). Config.E[411] makes `LightColorCapabilityCenter.b2` true, so the colour page's single colour and
  colour temperature go through `ColorModeControllerConfig.j`: one MultiController unit
  (`MultiController.c(value, 0x6e, ControllerMode.e([0x15]))`), the 0xA3 packets of `Config.gu`'s value (Bytes.j
  `[01] RGB [00 x5] <mask>` for an RGB pick, Bytes.t `[01] ff ff ff <K> <tint> <mask>` for a colour temperature
  or a Kelvin-tint pick), its mask every piece's bit with no cap (`BleUtils.X`), then `33 05 15 00`.
  - `build.color_rgb_frames` builds the unit (red at 15 pieces: `a3 00 01 02 6e 01 ff 00 00 00 00 00 00 00 ff
    7f ..`, `a3 ff ..`, `33 05 15 00 ..`); `build.color_temp_frames` is the whole-device colour temperature as
    the frames the app sends (`[color_temp(...)]` on every other scheme, byte for byte); `color_rgb`,
    `color_temp` and the segment builders refuse `h3010` with GoveeBleNotSupported (whole device only: the
    Part tab is account-sized, ColorModeH3010). The unit is the spec's `color_multi_value`, written at its own
    `len_value` with its `len_cap` mask; with no count it spans 15 pieces, H3010's before its first IC read.
    `build.color_mode_switch` is `33 05 15 00`; `build.piece_cap` is the most pieces a scheme's whole-device
    write carries (on `h3010` all one 0xA3 upload's mask can span, 34584, as `BleUtils.X` has no cap; 112, as
    before, elsewhere), which bounds a live `Device.segment_count` after the row's own IC rule (4095 on 411)
    and `Device.set_segment_count`, so a count the device reported always passes back in.
  - `GoveeConnection.send_many` takes a run of 0xA3 frames, from its START, directly before a write as one
    unit (`AbsControllerNoEvent4Multi`): the run streamed paced (`UNACKED_SEND_GAP`) with no drain between,
    then the write, and the unit done on its `a3 <comm>` ack: a whole 20-byte frame whose byte 3 is 0 accepts
    it (`onResult` at `MultiController.b()` = 3), and anything else raises GoveeBleCommandRejected; the write's
    own `33` ack is not the unit's. No ack: the whole unit goes once more on a fresh link, then
    GoveeBleTimeout. Every other frame list sends as before (each write ack-verified), and any other non-write
    frame is still refused. `wire.parse.multi_unit_comm` / `parse_multi_ack` read the START's comm byte and the
    ack (`upload_ack.is_multi_ok`) through the spec.
  - `Device.set_color_temp` and `turn_on(kelvin=)` take `color_temp_frames`: one frame still goes out with
    `send`, the unit with `send_many`.

  Admitted by default (`app_traced`): h7020 **H3010 / H3011 / H3012 / H3013** (411), whole-device control
  (power, brightness, RGB, colour temperature), with the 4702ABC connect's aa 40 reads around the status read
  (above) and `ColorPieceConfig.b`'s `max(15, IC)` pieces for the writes, the read-back over the IC itself.
  Source-traced, not live-verified: no H301x is on the bench, and which ack its firmware sends to end the unit
  is the app's (`a3 6e`, byte 3 = 0), not a capture's. Every SKU the table had before resolves to the same
  profile, field by field, and sends the same frames (the golden-frame suites pass unchanged).
- **The aa 0f string count sizes the live piece count on the h7020 bulb-string rows**
  (`DeviceProfile.light_num_per_unit`, keyword-only, default 0: no read; a devices.yaml row's
  `light_num_pieces {per_unit}` with the `light_num` connect read). Their screen reads `aa 0f` at connect
  (`NewDetailVM.s5`: `Controller4BulbNum`, byte 0 a Java byte) and stores the string count times the bulbs per
  string as its IC (`NewDetailVM.u5`: `Support.getBulbStringSetting` [1], 15, 25 on goodsTypes 100 / 102 at
  their pact (1,2), 10 on 161; on 149 the count itself), which its colour strip takes at each pick
  (`ColorPieceConfig.c`), and walks `ceil(pieces / group)` aa a5 groups (`NewDetailVM.D5`). The poll reads it
  between aa 04 and aa 05, as the screen's connect places it, and adopts the pieces as `Device.segment_count`:
  the whole-device mask and the aa a5 walk. Before the first count a write that carries a mask reads aa 0f
  first and raises GoveeBleError, sending nothing, without one, as H6062's aa 41 does, and the walk is skipped
  until a count is known. The screen stores whatever count it reads, and the library takes it as the screen
  does: past 112 pieces the writes take the 112-position mask (the same bytes: the 0x15 colour bodies cut it at
  7) and the walk takes them all, as on the aa 40 live count; 0 is the app's empty pick (two zero mask bytes,
  no walk); only a negative count (a Java byte, on which the app's pick throws) is none. The string-count
  setting reaches none of these (it offers at most 50 pieces). `build.light_num_query` and
  `parse.parse_light_num` are the read and its reply.
  - Admitted by default (`app_traced`): h7020 **H7020**, **H7028**† (53), **H7021** (54), **H7015** (62),
    **H7016** (63), **H7041** (100), **H7042** (102), **H7029** (149) and **H7033** (161): power, brightness,
    RGB and colour temperature on the h60a6 frames, over the live count, with the colour read-back. Segments are
    left out, as on H7025: the screen's per-segment strip is the account record's segment count wide.

  Every other SKU resolves to the same profile, field by field, at every pact its rows list, at (99, 99) and at
  none, with and without `allow_modelled`; no admitted SKU's frames or reads change. Not hardware-verified: none
  of these SKUs is on the bench.
- **A read-back piece count apart from the write count, and a SKU's own piece rule and support level within
  its row** (pieces-readback-vs-write-count). The h6609 260 screen (H70C4NewDetailActivity) walks aa a5 over
  `H70c4Support.b(SKU, IC)` pieces (`DetailViewModel.T5`), where its colour strip's position set, and so every
  write's mask, is `ColorPieceConfig.b`, sized once (`H70C456789ColorMode`'s constructor; the resize `T()` has
  no caller): 20 against 10 at IC 1000 for H70C4 / H70C5 / H70C7 / H6870 / H80C4, the same count at any IC for
  the other SKUs.
  - Spec: devices.schema.json's `ic_pieces` table form gains `readback_only` (the walk alone; a read that
    gives no count walks the static pieces), and `clientModel` (`by_model`) gains `ic_pieces` and `support`,
    which replace the row's for that SKU, on a SKU one row of the family lists, as `connect_writes` does (the
    generator refuses them on any other SKU). `govee_reference.client_row` applies them.
  - Library: `DeviceProfile.color_readback_ic_walk_only` (keyword-only, default False; with
    `color_readback_live_ic` and without `color_writes_live_ic`), which a readback-only rule sets, so the
    poll's one aa 40 read (where the discarded read was) sizes the walk and never `segment_count`. On it a read
    with no count walks the static pieces (`_readback.walk_pieces`), where a live-IC profile skips the walk, and
    a walk of other than `segment_count` pieces is made but not applied to `state.segments`: the strip applies
    a colour or brightness read-back only of its own length (`H70C456789ColorMode.updateColor` /
    `updateBrightness`), so at IC 1000 the app walks 20 pieces and shows none. A profile without the field,
    such as a 1.6.1-style one with `color_readback_live_ic` alone, still skips the walk without a count.
    `_resolve.sku_support` gives a route's level.
  - **H70C9 / H7084 / H6871 / R6871** and, by the catalogue, **H80C5** (260) are promoted to the default table
    (`app_traced`): their walk and writes are the static 20 / 10 / 15 / 15 / 10 pieces at any IC, as the app's.
    Their profiles are unchanged but for `DeviceProfile.skus`: H7084 and H80C5 no longer share theirs with
    the five IC-gated SKUs below (a grouping change; every other field, and every frame, is the same).
  - **H70C4 / H70C5 / H70C7 / H6870 / H80C4** stay behind `allow_modelled`, now walking 20 pieces (5 groups)
    at IC 1000, whose read-back is not applied (above), and 10 otherwise, the writes still 10. They are
    modelled for one inferred fact, as H6609 (173): the app's IC there is the one its pairing aa 40 read stored
    (`DefInfo.G0`; the connect aa 40 goes to `Info4Detail.v`, which nothing reads), and the library applies
    the table to the connect aa 40.
  - H6065 (h6061 121) keeps one count for both: its read-back (23 pieces) and writes (one panel) differ only with
    no layout stored, and with a stored layout L both are L (`CubeVM4Light.l5`, `realMyColorPiece`), so a 23 / 20
    split would be a pair the app never sends; its count is a full layout's, the layout editor's 20 (the Glide
    panels, above).

  Source-traced, not live-verified: no 260 SKU is on the bench. No Bluetooth was used.
- **H707A / H707B / H707C / R707A / R707B / R707C** (h707a 326, the H707ABCDetailVM screen), admitted by default
  (`app_traced`) at pacts (1,1) / (2,1) / (3,1): power, brightness, RGB, colour temperature ([2700, 6500], [1000,
  10000] at (3,1)) and segments, on the h60a6 frames (`Config.x[326]` = 0 -> `Yt`; `15 02` segment
  brightness). These outdoor strings are cut by the user, so the screen sizes every mask by the segment count
  the device reports in its 0xAC status read, TLV 0xA1 value[0] (pieces-segment-info-tlv-a1;
  `H707AInfo4Detail.G0` -> `Info4BleIotDevice.P0` and `SegmentIcDataCache`, then the colour page's resize,
  `H707AColorMode.X`). `DeviceProfile.status_segment_count` (keyword-only, default False; a status row that
  requests `cmd_a1`): `Device.segment_count` becomes each status read's count (at most 112; a count of 0, which
  the page skips, leaves it), and a masked write before the first read makes the status read first and raises
  GoveeBleError, sending nothing, when the device reports none, as H6062's aa 41 does. A reply without TLV
  0xA1 takes TLV 0x40's segment byte (the IC-pair reader's second value, above 0) where its IC differs from the
  one the screen holds, as the reply's lambda does (its 0x40 branch -> `n6`); the library holds each read's
  0x40 IC and 0xA1 IC sum, and before a Device's first read none, where the app holds the account's stored IC,
  so a first reply without 0xA1 takes the byte where the app, for an unchanged IC, keeps the account's count
  (the row's doc). The status read is the app's `ac 03 08 40 41 a3 a1 48 a5 45 16`; `DeviceProfile.segments`
  (the uncut 6 / 9 / 12 by suffix, `Support.f`) is only the count before it. `reassemble.StatusReply` carries
  the TLVs' facts: `segment_count` and `segment_ic_total` (0xA1) and `ic_segment` (0x40). The field needs
  0xA1 in `status_read` and `status_decode` `f_a1` `segment_info` (GoveeBleInvalidValue otherwise).
  Not read back: the TLV 0xA5 gradient record (no wire form; skipped, as on H3751), so the colour state is not
  read. Not sent: the `33 09` time sync the screen writes before its status read (as on the other status rows).
  The aa 06 / aa 07 device-info reads are unverified extra reads here (status-row-versions-from-tlv07).
  Source-traced, not live-verified: none is on the bench. No Bluetooth was used.
- **A SKU's own connect reads within its row** (`clientModel.connect_reads`, the later part of
  per-sku-row-facts), and the two dreamcolorlightv1 rows they unblock, admitted by default (`app_traced`). The
  rgbiclight AcNewDetail screen's connect read (`ControllerStrategyConfig.E1`) adds the aa 40 IC read by SKU
  within one goodsType: where `supportBindWithIcNum` holds (H61A0 / H61A1 / H61A2, the `Jump.b` list) and
  where `supportIcFresh` does (H61B5 at every pact; 123's others only at (2,2) / (3,x)); `VM4Light.N5` then
  walks the reply's section sum. A SKU's `by_model` `connect_reads` replaces its row's, on a SKU one row of the
  family lists (as `connect_writes`, `ic_pieces` and `support` do), and `govee_reference.client_row` applies it.
  PR #45 left these two rows un-narrowed because a narrowed row would have served those SKUs without their read.
  - **dreamcolorlightv1 71, narrowed to pact (3,2)**: **H618C / H619B / H619C / H619D / H619E / H61A3 / H61A5**,
    **H61A0 / H61A1 / H61A2** (their aa 40 and section-sum walk, as H61A9's) and **H6172**† / **H6173**† /
    **H618E**† / **H618F**† / **H61B2**†. At (3,2) the read-back groups are 4 (`supportRgbWithBrightnessOnePkg4Colors`)
    and the colour page's segment-brightness flag is on (`Config.I[71]`'s `UN`); the app's (2,1) / (2,2), with
    3 a group and the flag on a firmware gate, are refused.
  - **dreamcolorlightv1 123, narrowed to pact (2,1)**: **H61E0 / H61E1 / H80B5**, **H61B5** (its aa 40 and
    section-sum walk) and **H61B1**†. At (2,1) the aa 40 read is H61B5's alone; the groups are 4 for H61B5 /
    H80B5 and 3 for the others (`getOneGroupColorSize`, wire `by_model`). Not modelled: H61E0 / H61E1's
    low-blue-light read `aa ba 01` from soft 3.00.57.

  Both rows: power, brightness, RGB, colour temperature ([2000, 9000]), segments (15 pieces, `15 02`) and the
  gradual flag on the h60a6 frames, an aa 01 keep-alive, and the reads in the library's fixed poll order, as on
  rgbiclight 141 and H61A9: aa 05 before aa 40, aa a3 after the aa a5 walk and the device-info reads last, where
  the app sends aa a3, aa 40, then aa 05 and the walk (`ControllerStrategyConfig.E1`), an order divergence each
  row's doc names; following the app's connect order is a library-wide item. Part brightness is on for every SKU
  here (none is in the firmware-gated `g1` / `i1` lists), so the records carry brightness. Source-traced, not
  live-verified: none of these SKUs is on the bench. No Bluetooth was used. H6171 (barelightv1 17, the fourth
  `supportBindWithIcNum` SKU) stays off its row: that row reads the groups as `bulb_batch_v2` batches, whose
  walk has no section sum, and moving H6145 / H6146 / H6147 to the direct walk would change admitted SKUs'
  read-back.

### Changed
- **H6061 (h6061 51) is sized at 20 panels, the app's layout-editor cap, where it was 23** (the user's decision D1
  (a), 2026-10-22). The app sizes its masks and read-back from the user's stored layout L
  (`CubeColorModeV2.realMyColorPiece:505-508`, `CubeVM4Light.l5:386-422`), which the device never reports, and the
  row's count stands in for a full layout. The layout editor caps an H6061 layout at 20 hexagons
  (`Support.getShapeViewType:334-354` gives shape type 0, `Constants.o:400-402` gives 20;
  `BasicShapeBusiness.x:989-991` / `x0:1017-1019` show no add point at the cap, and `SplicePreviewAc.d1:189-192`
  refuses one), so 23 (`Support.getBulbStringMaxNum:180-194`, the read-back default with no layout stored) is no
  layout the app can store. The bytes change at a full layout:
  - the whole-device colour-temperature mask (`Bytes.u`) goes from `ff ff 7f` to `ff ff 0f`, and the whole-device
    RGB, which 1.6.1 sent as six `15 04` group frames, is one `Bytes.l` frame at `ff ff 0f` (Fixed);
  - the `aa a5` read-back walks 5 groups, not 6;
  - `Device.segment_count`, the profile's `segments` and the panel bound go from 23 to 20 (a count above 20 from
    `Device.set_segment_count` is refused at write time, as one above 23 was).

  `wire.build`'s `h6061` default count (`H6061_MAX_PANELS`) is 20 too. H6061's profile now also serves H6067 and
  H6065 (Added), whose facts it shares at 20 panels (`DeviceProfile.skus`, a grouping change). A smaller layout
  is exact only with the account's layout (D10, a later release). Source-traced, not live-verified: no H6061 is
  on the bench.
- **On the screens whose 0x0d report the app decodes as `[r, g, b][kelvin]` with no Kelvin test
  (`rgb_kelvin_0d_nocct`), a reported colour that is one of the app's Kelvin tint colours, inside the device's
  range, now reads as that colour temperature, as the app shows it** (CLAUDE.md rule 3; the user's decision 25,
  2026-10-22). The decode (the 0x0d strategy of the h70bx, h70b3 and h6840 `Info4Detail`s) stores the
  report's Kelvin and never tests it, and the library reported the RGB. The app's colour page shows the
  report's one colour as the colour temperature `KelvinConfig.c:119-122` of it when `KelvinConfig.i:193-200`
  holds (a table colour whose Kelvin lies in the range, inclusive), else as that colour:
  `DelegateCurMode.createView:185-203` ->
  `ColorViewModel.initColor4Device:1182-1224` -> `DelegateColorV2.w1:666-730` -> `W1:1105-1123`. So `state` now
  holds `color_temp_kelvin` with `rgb_color` unset for such a report, by the same test an RGB pick's write
  uses (`devices._fold`), from the status reply's TLV 0x05 and from an `aa 05` reply alike. The report's Kelvin
  bytes are still not read. Any other colour, and a tint outside [2000, 9000], stays RGB.
  - Admitted by default and changed: H6800 (h6800 272), H70B1 (h70b1 134), H70BC (h70bx 216), H6840 / H8840†
    (h6840 278), H6841 / H8841† (325), H6842 / R6842† (381), H6843 (382), H70B6 (h70b6 329) and H70B8 (341).
    The fold also promotes the h70b3 rows (Added); H70B6's profile now also serves H6810, H6811 and H8811,
    whose facts it shares (`DeviceProfile.skus`, a grouping change; no other admitted SKU's profile changes).
    Their rows come first in devices.yaml, so the profile's first SKU is now H6810, not H70B6: a `Device` built
    from H70B6's profile without `sku=` names itself H6810 and checks an advertised pact against H6810's rows
    (`create_device` and `make_device` always pass the SKU). H6061's and H617C's profiles keep their first SKU.
  - State only: no frame changes. Where the firmware echoes the tint of a `Bytes.p` write (a colour-temperature
    write, or an RGB pick of a tint colour) in the report, the device now reads back as the Kelvin that was
    sent; whether it does is not hardware-checked. Not live-verified: none of these SKUs is on the bench.
  - The next entry extends the fold to every screen with the stock colour page; these keep it.
- **On every screen with the stock colour page (the H703A / H703B's excepted for now, below), whatever its
  decode, a reported colour that is one of the app's Kelvin tint colours inside the device's range now reads as
  that colour temperature, as the app shows it** (the user's decision 27, "Extend, match the app", 2026-10-22;
  CLAUDE.md rule 3). The fold above ran on the
  `rgb_kelvin_0d_nocct` decode alone, but the test is the colour page's, not the decode's: every newdetail screen
  whose view model is an `IColorModeFuc` gets `DelegateColorV2` from `DelegateCurMode.createView:185-203`, and its
  `w1:666-730` -> `W1:1105-1123` shows a report of one colour as the colour temperature `KelvinConfig.c:119-122` of
  it when `KelvinConfig.i:193-200` holds, else as that colour. Only `DeviceDetailParams.Build.U`
  (`DeviceDetailParams.java:441-445` -> `DeviceDetailConfig.l:614-617` -> `R:275-285`) replaces that page, and in
  7.7.02 only rgbiclight h6160 `DetailConfig$init$4` sets it, for H6163 / H6117 at goodsType 0 (no row with
  support), with `DelegateColor4H6163`, a `DelegateColorV2` that keeps `w1` / `W1` (`m:75-82`). The cubelight
  screen sets its own colour strip (`CubeNewDetailsConfig`: `Build.V`, `CubeColorModeV2`), not its own page. So
  the library now folds where the row's `color_page` is `stock` (Added; Spec), from an `aa 05` reply and a status
  TLV 0x05 alike, and H7061's two heads go through the same rule. A decode that takes a Kelvin above 0 as colour
  temperature already reported none; what changes is a report that decodes to an RGB which is such a tint, as
  the colour of a `Bytes.p` colour-temperature write is (whether a device echoes it is not hardware-checked). Any
  other colour, a tint outside the range, and the three rows without a colour page (`color_page: none`: H8015's local mode list is illumination
  only, `VM4Light.C3:401-407`; H6071 / H6075's bundled one has no colour page) read as before. Where the same
  read brings the segments, the tint folds only when every segment shows it (the user's decision 29, "Fold only
  if segments agree", 2026-10-23). `W1` tests `SubMode4Color.d`, all its pieces equal to the first
  (`ColorUtils.q:335-349`); when they differ it sets neither the colour nor the Kelvin (`w1:732` -> `S1:1084-1090`
  shows the pieces). `d` holds the report's one colour (h60a1 `Info4Detail.Q:416-420`, cubelight
  `CubeNewInfo4Detail:146`) until the same read's groups replace it at the piece count (`SubMode4Color.j:113-148`):
  a status burst's TLV 0xA5 groups, which the reply carries after its 0x05 and the app applies in reply order
  (`Compose4BaseInfoSingleRead.u:247-345`), and a poll's `aa a5` walk, which the app reads in the same compose
  after the `aa 05` (`Compose4InfoBleIot$iControllerStrategy$1.v:125-132`; h60a1 `VM4Light.h5:327-340`, cubelight
  `CubeVM4Light$connectBleSuc$2.l:47-60`) before it shows the page. So the library shows the poll's report again
  once the walk is in (`_fold.apply_mode_over_pieces`). Segments that all show one colour other than the
  report's are what the page shows, as `W1` shows `d[0]` (CLAUDE.md rule 3): that colour as `rgb_color`, or, when
  it is a tint in range, its colour temperature, remembered as `last_color_temp_kelvin` as the app syncs it
  (`ColorViewModel.initColor4Device:1211-1222` -> `KelvinConfig.f:144-157`). Segments that differ among
  themselves keep the report's `rgb_color`, with no Kelvin remembered for it (the page shows no one colour); a
  read that brings none (the H60A6 at pact (1,1), whose own profile has no segments, Fixed, and whose status read
  asks for no 0xA5) folds as before, as the app tests the report's one colour there. Segments held from before
  the read (a write's, an earlier read's) are not tested, and a poll whose `aa 05` goes unanswered shows no
  earlier report over its walk. Segments that all show one colour are shown over a 0x15 report too, which
  carries the gradual flag and a Kelvin and no colour (the user's decision 30, 2026-10-23). The segmented rows
  write a colour as the 0x15 segment frame, and the bench H60A6's status bursts report `05 04 15 01 00 00`
  (`tests/test_wire_reassemble.py`). Both screens show it on the
  colour page (h60a1 `Info4Detail.K:215-224`, cubelight `CubeNewInfo4Detail.M:297-306`) and leave
  `SubMode4Color.d` to the same read's groups: h60a1 `Info4Detail.Q:399-407` (the `Info4Detail` of the H60A1 /
  H60A4's `VM4Light`, of the H60A6 / H1232 / H1252's `VM4LightH60A6:150` and of the H1270's
  `VM4LightH1270:145`) takes only the flag and the Kelvin, and cubelight `CubeNewInfo4Detail.U:488-537` takes
  nothing. So where those segments all show one colour, `W1` shows it whatever the report's Kelvin, and so does
  the state now (`_fold._shown_over_pieces`), read as above; a Kelvin above 0 is remembered as
  `last_color_temp_kelvin`, as `Q:401-404` holds it, unless the segments' tint replaces it. Until now the report
  read as decoded: a Kelvin above 0 as `color_temp_kelvin`, while a Kelvin of 0 (and the Glide panels' decode,
  which reads nothing from 0x15) left `rgb_color` as it was, so a colour set in the Govee app showed in `segments`
  only, as on the released H60A6 and H6061. Over segments that differ, or with none, it still reads as decoded.
  On the h60a1 screen's rows (H60A1 / H80A1, H60A4 / H80A4, the H60A6 at pact (1,2) / (1,3); behind
  `allow_modelled` H1270, H1232, H1252) a 0x0d report that decodes to a Kelvin above 0 is shown over the same
  read's segments the same way (CLAUDE.md rule 3). `Info4Detail.Q:416-426` holds that Kelvin and sets
  `SubMode4Color.d` to its one tint, which the groups then replace (`SubMode4Color.j:124-126`; the walk's
  `VM4Light.Z4:181-185` and the bursts' `o` / `p` handlers call it after any report), so where the segments all
  show one colour `W1` shows it, or its colour temperature if it is a tint in range, and where they differ it
  shows neither and the report reads as decoded. `last_color_temp_kelvin` is the report's Kelvin, as `Q:423`
  holds it, unless the segments' tint replaces it (`KelvinConfig.f:144-157`). Until now such a report read as its
  Kelvin whatever the segments showed. Elsewhere a 0x0d report of a Kelvin above 0 still reads as decoded: on the
  Glide panels the walk keeps the report's tint in `d` while its Kelvin is above 0 (`CubeVM4Light.m5:424-438`),
  so their page shows that Kelvin. Wherever the page's colour is shown, black counts as (1, 1, 1), as on the
  page (CLAUDE.md rule 3): `ColorViewModel.initColor4Device:1192-1210` posts a copy of `SubMode4Color.d` with
  `ColorUtils.N` (1, 1, 1) in each black colour's place, and that copy is what `W1` tests and shows. So segments
  that are all black show `rgb_color` (1, 1, 1), not (0, 0, 0), and black and (1, 1, 1) segments together count
  as one colour, (1, 1, 1), where they read as differing until now. H7061's two heads are such pieces:
  `H70606162Info4Detail.I:247-261` puts them in `d` (`SubMode4Color.p`), and its page starts from that
  `SubMode4Color` (`H70606162DetailVM.getInitColorData:589-591`), so two black heads show (1, 1, 1), and so do
  a black and a (1, 1, 1) head, where they showed no colour. So does a black report where the same read brings
  no segments: `d` is then the report's own one colour, which every stock-page decode stores opaque, as
  `ColorUtils.I` / `H` of the report's bytes, so a black one is `I(0, 0, 0)` and the copy replaces it (h60a1
  `Info4Detail.Q:417-420`; cubelight `CubeNewInfo4Detail:146`, homelightv1 `Info4Detail:118`, h6022
  `Info4Detail:115`, h7040 `Info4Detail:154` and `H6055InfoDetail:115`; bulblight
  `ModeParseStrategyConfig.a:60` and rgblight `ModeParseStrategyConfig.a:42`, a negative Kelvin's tint
  included; `H6052InfoDetail:118`; `H60501InfoDetail:137-143`; h70bx `Info4Detail:129-131`, h6840
  `Info4Detail:125-127` and h70b3 `Info4Detail:153-155`; each screen's `getInitColorData` hands the page that
  `SubMode4Color`). So the H6052's `0d 00 00 00`, for one, reads as `rgb_color` (1, 1, 1), not (0, 0, 0), on
  every row with the stock page; `color_page: none` rows and rows without a page read it as decoded. `segments`
  keep the black the device reported, and `last_color_temp_kelvin` is as before (the Kelvin sync, `:1211`,
  tests the original `d` with `KelvinConfig.f:144-157`, and black is no tint).
  - Admitted by default and changed (* = admitted at 1.6.1): bulblightv3 **H6005*** (20), **H6004* / H6006* /
    H6008* / H6009†* / H600A†* / H600B†* / H600C†* / H600D†* / H6010†* / H6011†* / H6013†* / H800B†*** (64),
    **H1501* / H601A* / H601B* / H601C* / H601D* / H801A* / H801C* / H801D* / R1501*** (111), **H14C0 / H14A1 /
    R14A1 / H14C2 / R14C2 / H14C1 / R14C0 / R14C1** (342), **H1401* / R1401*** (348), **H14B1* / R14B1†*** (408);
    tablelampv1 **H6052*** (22); h6188 **H6188*** (0); h6061 **H6061*** (51), **H6066** (96), **H6067** (97),
    **H6065** (121), **H606A** (163), **H6063** (228); h6069 **H6069 / H8069†** (284); h60ax **H60A1 / H80A1**
    (235), **H60A4 / H80A4** (302), **H60A6*** (303); h7040 **H7040*** (36); h6022 **H6022 / H8022** (249);
    hollowlamp **H6050** (27), **H6051** (37), **H6055** (42), **H6073** (58); carlightv1 **H6118 / H6194** (6,
    39); tvlightv1 **H6179** (12, 39); h6114 **H6114** (39); h613839 **H6138 / H6139** (39); h6181 **H6181**
    (39); h6159 **H6159 / H6110 / H6109 / H614B / H614E** (40); h6160 **H6160** (40); h6182 **H6182** (40);
    stringlightv2 **H6121 / H6141 / H6142 / H6154 / H6195** (1), **H6170 / H6196 / H613G†** (2), **H615A /
    H615B / H615C / H615D** (40), **H616B** (68), **H6178** (78), **H615E** (108), **H6192** (143). 86 SKUs, 31
    of them released in 1.6.1. A black report with no segments also changes on the 18 `rgb_kelvin_0d_nocct`
    SKUs of the entry above, none of them released in 1.6.1: h6800 **H6800** (272); h70b1 **H70B1** (134); h70bx
    **H70BC** (216); h6840 **H6840 / H8840†** (278), **H6841 / H8841†** (325), **H6842 / R6842†** (381),
    **H6843** (382); h70b6 **H70B6** (329), **H70B8** (341); h70b3 **H70B3** (229), **H70B4** (230), **H70B5**
    (231), **H6810** (255), **H6811 / H8811†** (256). That makes 104 SKUs for the black report.
  - Behind `allow_modelled`: h60ax **H1270** (346), **H1232** (356), **H1252** (373); homelightv1 **H6148** (16).
  - Not yet the H703A / H703B (h61d3 263, behind `allow_modelled`). Their screen shows the stock page too
    (`H703ADetailActivity`'s `VM4Light` is an `IColorModeFuc`, `VM4Light.java:119`; `ConfigDetail.a:63-106`
    registers colour strips only, `Build.V`; the bundled mode list `[15, 9, 10, 8, 11, 12, 20, 23]` holds page 9),
    and their colour is the status read's one-record TLV 0xA5
    (`VM4Light$afterConnectedSingleReadDeviceInfo$1.n:114-124` -> `SubMode4Color.l:180-201`), which `W1` shows as
    its Kelvin when it is a tint in [2700, 6500] and the burst's 0x15 Kelvin is 0 (`Info4Detail.W:568-578`
    replaces it only for a Kelvin above 0). The library still reads such a record as `rgb_color`, and the row
    has no `color_page` (its `kelvin_15` decode reports no colour): a folded Kelvin would be cleared by the
    keep-alive re-read's `aa 05` (a 0x15 Kelvin of 0) while that re-read's `aa a5` group is read and not
    applied, so the fold goes with the H703A / H703B status read-back step in the next release (decision 27).
    Not live-verified.
  - For example the H6052, whose screen decodes its 0x0d report as `[r, g, b]` only: a report of a tint colour in
    [2000, 9000] now reads as `color_temp_kelvin`, not `rgb_color` (README). The live report of a colour
    temperature set elsewhere, `0d ff ff ff 0a 8c`, is white, no tint, and stays RGB, as the app shows it. On
    the H60A6 it is a 0x0d report (pact (1,1)). Its 0x15 reports carry a Kelvin: unchanged at pact (1,1), whose
    read brings no segments, and at pact (1,2) / (1,3) shown as the burst's segments where they all show one
    colour (above).
  - State only: no frame, refusal or profile change but the new field (checked against fd770ff over every SKU
    either tree routes, with and without `allow_modelled`, with no pact, an unknown one and each listed pact, and
    crafted `aa 05` / TLV 0x05 reports at tint colours). Decision 29 was checked the same way against e3c51ba,
    with walks and TLV 0xA5 groups that agree with the report and that differ: no frame, refusal or profile
    change, and the state changes only with segments that differ, on the 14 segmented stock-page rows (H60A6,
    H60A4 / H80A4, H60A1 / H80A1, H6061, H6063, H6065, H6066, H6067, H606A; behind `allow_modelled` H1270,
    H1232, H1252). Showing the segments' colour was checked the same way against 3f7f4b3, with segments that
    agree with the report, that all show another colour (a tint and not) and that differ among themselves: no
    frame, refusal or profile change, and the state changes only where the segments all show another colour, on
    the same 14 rows. Showing them over a 0x15 report (decision 30) was checked the same way against 9602d85, with
    0x15 reports at a Kelvin of 0, above 0 and below 0, and 0x0d tint reports, over walks and TLV 0xA5 groups that
    all show 4000 K's tint, all show 3000 K's or 2000 K's tint, all show another colour, and differ among
    themselves: no frame, refusal or profile change, and the state changes only where a 0x15 report's segments
    all show one colour, on the same 14 rows. Showing them over the h60a1 screen's 0x0d report was checked the
    same way against e18f6f2, with 0x0d reports at a Kelvin above 0, of 0 and below 0 over the same segment
    sets: no frame, refusal or profile change, and the state changes only where a 0x0d report of a Kelvin above
    0 meets segments that all show one colour other than that Kelvin's tint, on the 8 SKUs of those rows.
    Showing black segments as (1, 1, 1) was checked the same way against e18f6f2 and that change, adding
    segment sets all black and black with (1, 1, 1): no frame, refusal or profile change, and the state changes
    only in `rgb_color` / `color_temp_kelvin` where the same read's segments are all black (now (1, 1, 1)) or
    black and (1, 1, 1) (now (1, 1, 1), no Kelvin), on the same 14 rows; `segments` and `last_color_temp_kelvin`
    don't change. H7061's heads were then checked the same way against that change, adding head pairs of black,
    (1, 1, 1), red and two tints: no frame, refusal or profile change, and the state changes only on H7061, in
    `rgb_color`, for two black heads ((0, 0, 0), now (1, 1, 1)) and a black with a (1, 1, 1) head (no colour,
    now (1, 1, 1)). A black report with no segments was then checked the same way against that change, adding
    black 0x0d / 0x15 / 0x0b / 0x02 reports at a Kelvin of 0, below 0 (a black tint too) and above 0: no frame,
    refusal or profile change, and the state changes only in `rgb_color`, (0, 0, 0) now (1, 1, 1), where the
    read brings no segments, on every stock-page SKU but H7061 (104 admitted by default, and H1270, H1232,
    H1252 and H6148 behind `allow_modelled`); `color_temp_kelvin`, `last_color_temp_kelvin` and `segments`
    don't change. Not live-verified: no read-back of a tint report was made on the bench.
- **A Device now re-learns its command channel from later advertisements, for its next session, as the app
  does.** Until now the cipher was fixed when the `Device` was built: its advertisement's encrypt flag, else the
  profile default. A device built before any of its advertisements was heard stayed on that default for the
  Device's life. Such a device is an H6008, whose default is plaintext but whose SW1.01.25 firmware encrypts and
  answers nothing else. The same goes for any SKU whose default is plaintext while its firmware encrypts.
  `Device.ingest_advertisement` now reads the encrypt flag too, whatever the advertised name, and hands it to the
  connection (new `GoveeConnection.set_advertised_encryption`, which returns the mode the next session uses).
  - **For test doubles:** a stand-in that replaces `Device._conn` now needs `set_advertised_encryption(mode)`
    returning an `Encryption`, because `ingest_advertisement` calls it whenever an advertisement's Govee entries
    agree on a flag that differs from the last one learned.
  - **When it applies:** from the next session on, never to the session that is up. `_prepare_session` is the
    only place a session's mode is set, from the advertised mode as it is when the setup starts. No link is
    dropped for it; a session that is up and answering already proves its cipher. `encryption` reports the
    session's mode while a link is up, and the one the next session starts from when none is.
  - **Merged records:** Home Assistant's record keeps every manufacturer-data key it has heard for the address
    (habluetooth merges `{**prev, **new}`, as BlueZ does), and the Govee header, with its encrypt bit, is part of
    the key. A header change, or another device's frame merged under the address, therefore leaves two Govee
    entries, the older one first. The flag is learned only when every Govee pact entry carries the same one. A
    mixed record keeps the cipher and logs one WARNING per spell, naming the headers. The constructor is
    unchanged (the first entry, as in 1.6.1).
  - **BGC:** a BGC `encryptVersion` of 1 or 2 still overrides the advertised flag (`isEncryptionSupported` = the
    flag OR the BGC), so a plaintext flag never downgrades a BGC-1 device; the INFO line says so. A cached BGC 2
    still refuses AES-GCM without a link. An AES-GCM mode given to the constructor with no BGC characteristic is
    now lifted by a later advertised flag. The BGC result is still read once per Device; the app reads it on
    every connect.
  - **What the app does:** `BleBroadVersionCache.d` overwrites a per-MAC flag, true or false, from each parsed
    broadcast: `AbsViewMode4OpWithOtaCheck.w2`, `AbsBlePact.t`, the H5083 screen, the add-device scan, and the
    group and OTA paths. Only a broadcast whose pact parses writes it (`GoodsType.parseBleBroadcastPactInfo`).
    `EncryptionUtils.isEncryptionSupported` reads it with the BGC on every frame read, written or notified
    (`EncryptionBluetoothGattCallback`, `EncryptWriter.encryptWriteCharacteristic`). The light screen writes it
    from the scan just before it connects, so in practice it holds for a session (`AbsViewMode4Op.onScanEvent`
    stops the scan, parses the matched broadcast, then connects).
  - **Live-verified** 2026-10-21 on both bench H6008s (SW1.01.25, no BGC characteristic), reads only:
    - built with no advert, the first session went out plaintext and none of its `aa` reads was answered;
    - after each bulb's own advert (header 0x43) was ingested, the next session handshook AES-RC4 and every read
      was answered;
    - a mixed record kept the cipher and warned.
  - Design: `govee-apks/library-audit/DESIGN_relearn_advert_cipher.md`.
- **A polled profile reads aa 04 only with `Capability.BRIGHTNESS`**, as the app's connect read does
  (`ControllerStrategyConfig.k1` adds the aa 04 read for H7017 and not H7004). Every polled profile the table
  had before carries the capability, so their reads are unchanged; a `DeviceProfile` built by hand with
  `readback="polled"` and no brightness no longer reads aa 04 or sets `state.brightness`. The resolver keeps
  the rule strict both ways: a polled row's `connect_reads` lists `brightness` exactly when the row has the
  capability.
- **Colour temperature on a piece selection only where the app's colour page has a Part-tab Kelvin item**
  (new keyword-only `DeviceProfile.segment_color_temp`, default True). The colour page's All tab sends its
  Kelvin over every piece (`DelegateColorV2.G0`). Only the Part tab's Kelvin item goes to the selected pieces,
  and `DelegateColorV2Part.Z0` adds it only where `LightColorCapabilityCenter.C2` holds. For every supported
  goodsType that is `SupportPartColorTemConfig.b`: the goodsType is in its list d (its pact-(2,1) test for 112
  adds nothing, as 112 is in d too). Where the row's new `segment_color_temp` is false,
  `Device.set_segment_color_temp` now raises GoveeBleNotSupported before any I/O. Its docstring no longer names
  H6062 for the aa 41 segment-count read: H6062 is refused first, so only a hand-built `section_count_read`
  profile reaches that read. `set_zone_color_temp` doesn't read the field: it sends a zone's own mask, as in
  1.6.1. `set_color_temp` and `turn_on(kelvin=)` don't change, and nor does a segment RGB pick that is a Kelvin
  tint colour, which the app sends as a masked Kelvin frame too (`Config.Yt`). So SEGMENTS and COLOR_TEMP
  together no longer mean `set_segment_color_temp` works (the integrator note under Added). **42 of the 104 SKUs
  admitted at 1.6.1 lose segment colour temperature** (40 by default, 2 behind `allow_modelled`):
  - dreamcolorlightv1: H61A8 (19), H617C / H617E / H617F (70), H617A (73), H618A / H802A (74), H619A (75),
    H6167 / H619Z (83), H6168 (136), H61BE (175), H6176 / H61BC (176), H61BA (178), H6175 (187), H616C /
    H616D / H616E (207), H6169 (262);
  - striplight: H6062 (41), H610B (114);
  - h7050: H7050 (46), H7060 (76), H7051 (105), H7062 (107), H7065 (113), H6088 (157), H7066 (171), H7052
    (218), H7053 (219), H7063 (227);
  - h61d3: H6640 (242), H6641 (247), H1AB1 (387), H1AB2 (388), H1AB3 (389), and H61B8 / H61B9 (299, behind
    `allow_modelled`);
  - h7056: H7056 (304);
  - h60ax: H60A6 (303), and h6047: H6047 (119), both on list-d goodsTypes, by the user's decision 24 (next
    bullet).

  The 21 that keep it are on list-d goodsTypes: H6042, H6087, H7055, H60B1, B7080 / B7081 / B7082, H6098 /
  H8098, H6690, H61B3 / H61B6, H1A42, H1A43 / H1A44, H1AA2, H1AA5, H1B6A, and H61F2 / H61F5 / H61F6 (behind
  `allow_modelled`). No table SKU's zone colour temperature changes: `set_zone_color_temp`
  doesn't read the field. Three profiles split on the fact: H1AB2 leaves H1AA5's, H7066
  leaves H7055's, and H7094 leaves H7057 / H8057 / H8066's. H1AA5 and H7066, second in theirs at 1.6.1, now
  come first in their own, so a `Device` built from H1AA5's or H7066's profile without `sku=` names itself
  after them, not H1AB2 or H7055 (`create_device` and `make_device` always pass the SKU). H1AB2's and H7055's
  profiles keep their first SKU, and H7057 / H8057 / H8066 / H7094 are new. Of the new SKUs, 102 have no
  segment colour temperature (97 by default, 5 behind `allow_modelled`) and 52 keep it (41 by default, 11
  behind `allow_modelled`); in all, 144 SKUs have none (139 / 5) and 73 keep it (62 / 11). The rows the
  1.8.0 work adds or promotes are none of them list-d goodsTypes, so each says false: h60ax 235 / 302, h61d3
  202 / 239 / 246, dreamcolorlightv1 204 and h7050 98 (H7061's two heads). None of them has zones. A row with
  a `color_temp_control` (the `whole_device_kelvin` profiles, Added) must say false where it has segments, and
  the resolver refuses true there, so `segment_color_temp` is the one fact an integrator gates a per-segment
  Kelvin on. Source-traced (Govee Home 7.7.02), not live-verified. H61A8's bench check (2026-10-17) sent its
  Kelvin over all 15 segments only, so no hardware observation supports a masked Kelvin on these rows.
- **H60A6 and H6047: no `set_segment_color_temp`, matching the app's Part-tab Kelvin** (the user's decision
  24, 2026-10-22). Both screens have the Part tab's Kelvin item (list d), but it widens every pick to a whole
  zone. On the H60A6 a selection that holds a ring piece goes to the whole ring 0-11, with piece 12 as picked
  (`ColorModeH60A6.canClickColorTem:519-541` over `F`); on the H6047 it goes to each bar it touches, 0-4 / 5-9
  (`ColorMode10Segment.canClickColorTem:655-691` with `q0` / `r0`). Neither Part tab's Kelvin item sends one
  to part of a zone, which 1.6.1's `set_segment_color_temp` sent as selected. Rows h60ax 303 and h6047 119
  now say `segment_color_temp: false`, so on both `set_segment_color_temp` raises GoveeBleNotSupported before
  any I/O. Whole-device Kelvin (`set_color_temp`, `turn_on(kelvin=)`), zone Kelvin (`set_zone_color_temp`,
  over the masks that widening gives: H60A6 main `00 10` / background `ff 0f`, H6047 left `1f 00` / right
  `e0 03`) and per-segment brightness are unchanged, and so is `set_segment_rgb`'s tint-colour pick: the
  app's RGB picker doesn't widen, and sends an in-range tint colour to the selection as it is, as a masked
  Kelvin frame (`ColorModeH60A6.canClickColor:503-516`, `ColorMode10Segment.canClickColor:613-652`,
  `Config.Yt`). A refusal only, with no frame change, so no bench run. An integration that gates on the
  field (the integrator note under Added) offers no segment colour temperature on the two; their zones keep
  it.
- Nothing else for existing SKUs (H6188's colour temperature, H6061's whole-device RGB, H60A6 at pact (1,1), the
  segment brightness of H6098 / H8098 / H6690 and the h61d3 IC-sized rows' live mask, decision 2, are under Fixed,
  and the 42 SKUs without segment colour temperature, the device-info reads of H60B1 / H7056 / H1630 / R1630,
  H8015's colour-temperature pair and a reported tint colour, which now reads as colour temperature on the stock
  colour page (31 of these SKUs, starred in the stock colour page entry), are in this section; H61B8 / H61B9 /
  H61F2 / H61F5 / H61F6, behind `allow_modelled` at 1.6.1, take the live mask and are admitted by default, Added).
  Each other SKU of the 104 admitted at 1.6.1 (98 by default, 6 behind `allow_modelled`) resolves to the same
  profile at every pact its rows list and at none, with and without `allow_modelled`, so its frames and decoded
  state are unchanged. Where a new SKU has the same facts it joins that SKU's profile (`DeviceProfile.skus`):
  H6118 / H6179 / H6194 with H6005, H6099 with H6098 / H8098 / H6690, H70A1-3 and thirteen dreamcolorlightv1 71 /
  123 SKUs (H618C / H619B / H619C / H619D / H619E / H61A3 / H61A5 / H6172 / H6173 / H618E / H618F / H61B2 / H80B5)
  with H6176 / H61BC, H61B0 with H61B3 / H61B6, H14C0 / H14C1 / R14C0 / R14C1 with H600A-H600D / H6011 / H6013 /
  H800B (H600A stays first), H6145-7 and, at pact (2,1), H611A / H6143 / H6144 with H617C / H617E / H617F / H6168.
  H6099 and H61B0 come first in theirs, so a `Device` built from one of those profiles without `sku=` names itself
  after them (`create_device` and `make_device` always pass the SKU). The pact gate on the BK rows, on
  straightfloorlamp 69, on the h70b3 rows, on h70b6 329 and on pact_tvlightv2 24 (Added) is new routing for new
  SKUs only: no SKU it covers was supported at 1.6.1.
- `wire.build` drops the h6061 group-frame constants `H6061_GROUP_SIZE`, `H6061_MASK_BYTES` and
  `OP15_SET_GROUP_COLOR` (not in `__all__`), which only the old ceil(n / 4) `15 04` RGB frames used (Fixed,
  H6061).
- **The released status rows on the aa 01 keep-alive follow it, as their screens do** (the user's decision 17,
  "Match the app"; `heartbeat_power`, Added). **H6076 / H8076 / R16D0** (straightfloorlamp 69), **H6069 / H8069**
  (h6069 284), **H617G / R617G** (rgbiclight 351), **H618G / R618D** (352), **H7086** (h7086 293), **H7025 / H8025**
  (h7020 296), **H7076** (h7076 313) and **H3700** (h3700 383), all admitted by default since PR #45, read the status
  on every poll until now. Now a poll makes the 0xAC read once per link, and on a link it already read nothing goes out:
  the power there follows each keep-alive reply, which stamps `Device.power_read_at` and reaches the callbacks. On
  69, 284, 351 and 352 a reply that changes the power sends the screen's re-read at once on that link: `aa 05`, then
  the `aa a5` groups (69: 1-2, 351 / 352: 1-4, both applied as the segments; 284: 1-35, the no-layout count, read and not
  applied). H703A / H703B (h61d3 263, behind `allow_modelled`) gain the re-read too (`aa 05`, `aa a5 01`, not
  applied). The other frames, the first poll's reads and the decoded state of a fresh link's poll are unchanged;
  their profiles also carry `color_readback_per_batch` 4 on the re-reading rows (it was 3, unused on a status row).
  Source-traced (Govee Home 7.7.02), not live-verified.
- **H6058 (hollowlamp 59) and H6071 / H6075 (h6071 57 / 67) send the app's empty `33 b2` check on every connect
  while no secret is set** (the user's decision D4, 2026-10-22, an approved connect-byte change;
  `DeviceProfile.empty_secret_check`, Added). Their screens put `Controller4Secret.Companion.a` of the account's
  `secretCode` first in the connect read with no secret test (`H6058NewDetailVm.T4:128-135`, the list at `:168`;
  `VM4Light$afterConnectedSingleReadDeviceInfo$1$4$1.initControllerNoEventList:126`, `:184`), and `""` there
  (`DefInfo:106-110`) gives `33 b2`, 17 zero bytes and the xor (`Encode.decryByBase64:29-40`,
  `BleUtils.p:1030-1041`). Every session without a secret sends that frame after the handshake, before any
  other, where the library's account-locked rows sent no unlock without one. A device that refuses it fails the
  session with GoveeBleAuthError, as the app's connect read fails; with a secret set the bytes are as before.
  `read_secret()`'s pairing read sends none: the app's pairing screens (`hollowlamp.add.PairAcV1` and
  `h6071.add.PairAcV1`, each an `AbsPairAc4SecretV1`) read the secret with no check
  (`AbsPairAc4SecretV1.T1:107-113`), so a session that `read_secret()` sets up sends none, and the next other
  operation on it sends the empty check first, an operation that overlaps the read included (it waits for the
  read). The three SKUs are new in this release (Added), so no released SKU's frames change: every other
  account-locked row (the plugs and hollowlamp 27 / 37) still sends no unlock without a secret.
  Source-modelled (Govee Home 7.7.02), not live-verified: no H6058, H6071 or H6075 is on the bench.
- **A whole-device colour or Kelvin write sets every segment the state holds.** Until now `set_rgb`,
  `set_color_temp` and `turn_on(rgb=...)` / `turn_on(kelvin=...)` left `state.segments` as they were, so the state
  held the new whole-device colour beside the segment colours from before the write until a read brought them back.
  On the keep-alive rows with segments (H6076 / H8076 / R16D0, H617G / R617G, H618G / R618D, above), whose poll reads
  no status again on that link, that lasted the link. Now each segment the state holds takes the picked colour, or
  for a Kelvin the app's tint for it (`Constant.getTemColorByKelvin(K)[2]`, `wire.cct.tint_for_kelvin`), its
  brightness kept (`_fold.fill_segments`), as the newdetail colour page's whole-strip pick selects every piece at
  that colour (`DelegateCurMode.createView:185-203` -> `DelegateColorV2.F0:220-235` / `G0:241-253` ->
  `ColorModeUtil.e:140-146`). A segment the state doesn't hold stays unknown, and a later segment read-back
  replaces them as before. This holds on every profile whose state holds segments, released ones included; no
  frame changes.
- The resolver serves `power_live: heartbeat_aa01` on a status row (`power_read: ac_status`), not only on a
  polled one. The library folds no keep-alive reply on either kind (but on a `heartbeat_power` row, Added): the
  poll's own power read stands in for it, aa 01 on a polled row and the status read's TLV 0x01 on a status row.
  `power_live` is a resolver gate only (no `DeviceProfile` field), so no profile, frame or decoded state moves.
- `DeviceProfile.status_read` may be empty: the bare status read `ac 03 00` (`build.status_query(types=())`).
  On such a profile the reply's TLV 0xA5 is read as colour groups where the row's `f_a5` is `color_group`,
  though it was not requested. Every status row admitted before requests at least one type, so its request
  and decode are unchanged.
- A SKU's `by_model` `connect_writes` replaces its row's (`DeviceProfile.light_effect_stop`), where a screen
  splits its connect write by SKU within one goodsType; the build refuses one on a SKU that two supported rows
  of its family list. No SKU admitted before has one.
- The resolver ties a row's `segment_brightness` colour-page flag (`color_functions`) to the `segments`
  capability only where the row's colour scheme has a segment-brightness frame (`h60a6`'s `33 05 15 02`,
  `Bytes.G`; `_resolve.SEGMENT_BRIGHTNESS_SCHEMES`), not on the flag alone. H6071 / H6075 (h6071 57 / 67) keep
  their traced flag: the app shows the slider (`Config.I[57]` / `[67]`), but there is no relative-brightness
  version for them (no `Config.y` / `Config.z` entry), so it sends no frame and the one-piece lamps have no
  segments. They are admitted on the `kelvin_v9` scheme with the Kelvin + brightness pair (Added). Every
  admitted row with the flag before them is on `h60a6`, so no profile changes. Since the segment split
  (Added) the tie is one way: a row with segments whose `color_functions` leaves the flag out offers no
  segment brightness (h61d3 202).
- **Every status row but the H60A6's and the H6076's takes its versions and device id from TLV 0x07 alone and
  sends no device-info reads**, as its screen does (the user's decision, 2026-10-22: "Skip them", option A of
  the `status-row-versions-from-tlv07` decision). That is 95 SKUs: 88 admitted by default and 7 behind
  `allow_modelled`. **Four of them were released at 1.6.1 and change:** H60B1 (h60b1 300), H7056 (h7056 304),
  H1630 and R1630 (h1630 364) no longer send `aa 06` / `aa 07 03` / `aa 07 02` on their first poll, by the
  user's decision for these four (decision 22, 2026-10-22: "Match the app"; their screens send none, e.g.
  `H60B1ViewModel.W4`, which takes the versions from TLV 0x07).
  - Each status row now names its `device_info_reads` (`devices.schema.json` clientRow). It is required on
    every row with `status_read` and refused on any other row. It has two values; the build refuses any other:
    - `none`: the screen's connect read (`Compose4BaseInfoSingleRead`) queues no aa 06 / aa 07 read, and its
      success callback takes the soft and hard versions from the reply's TLV 0x07
      (`Compose4BaseInfoSingleRead.w`, then `k2(e(), d())`, which sets them through `Info4BleIotDevice.g1`).
      Each row's `doc` cites its own compose and callback. Where the screen passes the compose a controller
      list, the `doc` names what it holds, and none of it is a version read: the day-sync reads (h60ax 346,
      h1741 365, h1630 364: `DaySyncVM.Companion.f`), `Controller4StopLightEffect` (h3700 383), and the
      room-feast and Wi-Fi OTA reads (h70b3 229-231 / 255 / 256: `VM4H70B345.u5`; h70b6 329 / 341:
      `VM4H70B6.i5`), and the aa 40 IC read ahead of the 0xAC read (h7020 316 / 391 / 411:
      `NewDetailVM$afterConnectedSingleReadDeviceInfo4702ABC$1`'s `Controller4IcNumber`).
    - `firmware`: the library's reads are a cited rule-6 extension.
  - `DeviceProfile.device_info_reads` (Added, above) is False at `none`. There, `Device.update` sends none of
    the aa 06 / aa 07 03 / aa 07 02 reads, nor the re-read of a version that a firmware-gated fact waits for.
    It folds the versions and device id from each burst's TLV 0x07, as it already did.
  - `none`, admitted by default:
    - released at 1.6.1: H60B1, H7056, H1630 and R1630 (above);
    - h6047: H6039 (277); h6069: H6069 / H8069 (284); h3751: H3751 (354); h7045: H7045 / H7046 (338); h7072:
      H3351 (371); h70dx: H70D1 / H80D1 (267), H70D2 (268), H70D3 (269);
    - rgbiclight: H6671 / H6672 (332), H617G / R617G (351), H618G / R618D (352); h6840: H6840 / H8840 (278),
      H6841 / H8841 (325), H6842 / R6842 (381), H6843 (382); h6609: H6860 / R6860 / H6861 / R6861 (362),
      H6350 (374), H6850 (378), H6351 (392); h6800: H6800 (272); h70b6: H70B6 (329), H70B8 (341);
    - h7086: H7086 (293); h7057: H7057 / H8057 (274), H7058 (275), H7093 (289), H7094 (290), H8066 (305),
      H8067 (393), H3200 (353), H7087 (315), H3501 (339), H3500 (345); h7020: H7025 / H8025 (296); h7076:
      H7076 (313); h3700: H3700 (383); h6092: H7071 (334), H7073 (333), H3860 (401);
    - the status rows 1.8.0 admits or promotes: h60ax H60A4 / H80A4 (302), and h6092 H7070 (283), whose
      `VM4LightH7070.T4` compose carries no controller list at any of its pacts (its callback `U4` takes the
      versions from TLV 0x07);
    - the status rows 1.9.0 admits or promotes: h705a H3401 (394), h7057 H3510 / H3511 (349), h7020 H702A /
      H702B / H702C (316), H7026 / H8026 (318), H7027 (337), H30D0 / H30D1 (391), H3010 / H3011 / H3012 /
      H3013 (411), h707a H707A / H707B / H707C / R707A / R707B / R707C (326), h70b3 H70B3 (229), H70B4
      (230), H70B5 (231), H6810 (255), H6811 / H8811 (256), and h7072 H7072 (294).
  - `none`, behind `allow_modelled`: h60ax H1270 (346), H1232 (356), H1252 (373); h61d3
    H703A / H703B (263); h1741 H1741 / R1741 (365).
  - The catalogue snapshot routes H8069, H80D1, R617G, R618D, H8840, H8841, R6842, H8057, H8025, H3511, H30D1
    and H8811. H7072 and H3351 share one screen path (`H7072VM4Light.o5` / `s5`, whose one goodsType branch is
    H2800's 396). H1232 and H1252 run the H60A6's screen, but the H60A6's evidence for its reads is its own
    firmware image and bench unit, so they are `none`.
  - Four SKUs' soft version feeds a `by_firmware` Kelvin gate, now from TLV 0x07, as on the screen (the
    gate's `KelvinConfig` entry reads `Info4BleIotDevice.a0`, which `k2` sets): H3751
    (`KelvinConfig.Config.o4`), H3200 (`.J3`), H3501 (`.h4`) and H3500 (`.i4`). No other `none` SKU has a
    firmware-gated fact, the four released ones included, so only their device-info fields can change.
  - `firmware`: H60A6, from the handlers in its sw 1.00.41 image, and H6076 / H8076 / R16D0, from the H6076
    bench (2026-10-21). They are the only status SKUs that send the reads.
  - **What changes on a released SKU:** H60B1, H7056, H1630 and R1630 send three fewer reads on the first poll,
    and their `DeviceState` device-info fields come from the status reply's TLV 0x07. If a unit's reply leaves
    TLV 0x07 out, those fields stay None (the app would keep the account's versions; the library has none).
    Their frames and decoded state are otherwise unchanged.
  - Source-traced (Govee Home 7.7.02), not live-verified: none of the 95 SKUs is on the bench.
  - README's `DeviceState` entry, and `DeviceState`'s device-info comment and `device_id` docstring, now say
    where device info comes from: the aa 06 / aa 07 03 / aa 07 02 reads, or on a status row the status
    reply's TLV 0x07, by the row's `device_info_reads`.

- The `legacy_02` scheme changes no other SKU: each of the 309 the table admitted before it (235 by default,
  74 behind `allow_modelled`) resolves to the same profile, field by field, at every pact its rows list, at
  (99, 99) and at none, with and without `allow_modelled`; the 10 `legacy_02` SKUs (Added) are the only new
  routes. One state rule it adds is not scheme-bound: a profile without `COLOR_TEMP` keeps an RGB pick that is a
  Kelvin tint colour as `rgb_color`, though its frame is the colour-temperature form, as the colour page keeps
  the picked colours (`ColorViewModel.u0`: `SubMode4Color.p`) and `syncKelvin` moves only an illumination tab
  (rgblight `VM4Light.syncKelvin`). Of the SKUs admitted before, only H6113 at its BK pacts (h6113 39, the h6006
  frames without colour temperature, the rgblight screen) has such a profile: after a pick of a tint colour in
  [2000, 9000] its state now holds that colour where it held the Kelvin. Its frames are unchanged.
- The colour-temperature work changes no other SKU: each of the 319 the table admitted before it (245 by
  default, 74 behind `allow_modelled`) but H60A4 / H80A4 and H8015 (Changed, below) resolves to the same
  profile, field by field, at every pact its rows list, at (99, 99) and at none, with and without
  `allow_modelled` (`white_type="rgb_white"`, whose frames are as before byte for byte, `whole_device_kelvin`
  and `kelvin_with_brightness` False, and an empty `mode_decode`). H60A4 / H80A4, behind `allow_modelled`
  before, take the pair (Added), and so does H8015 (Changed, below).
- For integrators: `Capability.SEGMENTS` with `Capability.COLOR_TEMP` no longer means the segment and zone Kelvin
  setters work. On a `DeviceProfile.whole_device_kelvin` profile (H60A4 / H80A4, H8015, H6071 / H6075,
  H61D2-H61D6, H60A1 / H80A1 and H61E5 / H61E6, all by default) `set_segment_color_temp` and
  `set_zone_color_temp` raise GoveeBleNotSupported, because the app has no Kelvin on part of those devices. Every
  such profile with segments also has `segment_color_temp=False` (its row says false, and the resolver refuses true
  beside a `color_temp_control`), so a per-segment control gated on `profile.segment_color_temp` (the integrator
  note under Added) offers none there, and none of them has zones, so a per-zone one (COLOR_TEMP and the profile's
  zones) offers none either; hass-govee-ble-local's segment lights need that follow-up (they offer colour
  temperature whenever the device has `COLOR_TEMP`). A profile built by hand with `whole_device_kelvin=True` is
  refused too, whatever its `segment_color_temp`. Of the 1.6.1 SKUs only H8015 (no segments or zones, so nothing
  else is refused: Changed, below) has `whole_device_kelvin=True`.
- **H8015 (bulblightv3 328) sends the app's colour-temperature pair and is admitted by default
  (`app_traced`)**, on the user's sign-off (2026-10-22: "Approve"). Since 1.6.1 it was served behind
  `allow_modelled` with the Kelvin frame alone. Its screen's only colour-temperature control is the
  illumination tab, which writes the Kelvin frame and then `33 04` <the brightness it holds> as one write
  (`View4IlluminationMode.I0`: `Config.N[2]` = `Config.GM` -> `Bytes.A`, then `BrightnessConfig.b` of
  `illuminationCur()[0]`, the aa 04 brightness). Its row now says `color_temp_control: illumination`, so:
  - `set_color_temp(4000)` with brightness 42 held sends `33 05 0d ff ff ff 0f a0 ff d5 a1 ...` and then
    `33 04 2a ...`, each acked in order, where 1.6.1 sent the first frame alone;
  - with the brightness unknown it reads aa 04 first, and with no reply it raises GoveeBleError and writes
    nothing;
  - `turn_on(kelvin=)` sends the Kelvin frame before its `33 04` (the given brightness, else the held one);
  - the profile is `kelvin_with_brightness` and `whole_device_kelvin` (H8015 has no segments, so nothing else
    is refused).

  Every other frame, read and decode is as before. Source-traced (Govee Home 7.7.02), not live-verified: there
  is no H8015 on the bench. Every other SKU of 1.6.1 keeps `kelvin_with_brightness=False` and its one-frame
  colour-temperature write.
- The read-back work changes no other SKU: each of the 323 the table admitted before it (247 by default, 76
  behind `allow_modelled`) but H61A9 and H61C2 / H61C3 / H61C5 (Added) resolves to the same profile, field by
  field, at every pact its rows list, at (99, 99) and at none, with and without `allow_modelled`
  (`color_readback_ic_sections` and `detail_power_read` False). H7061 is a new route.
- The transport reads a write's command through the frame reader (`wire.parse.write_command`, the write
  counterpart of `request_command`), for the ack it waits for: `GoveeConnection.send` with `expect_ack`,
  `send_many` and `send_once` check each frame with it before any I/O, and the ack-verified write takes its
  command from it. A `33` frame of 2 bytes (the proType and the checksum: no command) now raises
  `GoveeBleInvalidValue` before any I/O instead of being sent. No frame the library builds changes.
- The device-list card's row type changes no other SKU: each of the 324 the table admitted before it (249 by
  default, 75 behind `allow_modelled`) resolves to the same profile, field by field (`session="held"`), at every
  pact its rows list, at (99, 99) and at none, with and without `allow_modelled`. `identify.parse_broadcast_onoff`
  now reads the advert's switch byte through the spec (`power_state`, source `advert`) and gives the same on/off
  as before for every SKU. H70B2, H6630, H8630 and H6631 are new routes.
- The status read per pact changes no other SKU: each of the 328 the table admitted before it (253 by default,
  75 behind `allow_modelled`) resolves to the same profile, field by field (`status_read_by_pact` empty), at
  every pact its rows list, at (99, 99) and at none, with and without `allow_modelled`, so its request is as
  before. H7070 is a new route.
- `kelvin_v9` changes no other SKU: each of the 329 the table admitted before it (254 by default, 75 behind
  `allow_modelled`) resolves to the same profile, field by field, at every pact its rows list, at (99, 99) and
  at none, with and without `allow_modelled`, and every builder's frames on the six schemes before it are as
  before. `ColorScheme` (and `DeviceProfile.color_scheme`) gains the member, an additive change. H6071 and
  H6075 are new routes.
- The exact-pact pairing write changes no other SKU: each of the 331 the table admitted before it (256 by
  default, 75 behind `allow_modelled`) resolves to the same profile, field by field, at every pact its rows
  list, at (99, 99) and at none, with and without `allow_modelled`; the routes read the pairing items only to
  refuse H615A-E without a pact (no SKU a pairing item names was supported before). H615A-E join the profile
  H6159 heads (`DeviceProfile.skus`), whose facts they share; H6159 stays first. The warning a Device logs
  for a later pact none of its rows accepts no longer says the app takes another row there: it may run the
  unit on other facts (H615A-E at (65535, 255)).
- No SKU's resolved profile changes with the re-selection, and a Device whose advertised pact matches its
  route keeps its profile and frames. Only a SKU with two routes whose profiles differ just in what a pact
  picks can move: H6118, H6194 and H6179 (above). `Device.profile` is no longer fixed for the life of a Device
  on those three; read it after a pact change (the callbacks fire).
- For integrators: `Capability.SEGMENTS` no longer means `set_segment_brightness` works. Check
  `device.supports_segment_brightness` before offering brightness on a segment or zone entity:
  hass-govee-ble-local's segment and zone lights call `set_segment_brightness` for their brightness, and need
  that follow-up before H61D2-H61D6 are set up. Every SKU admitted before has `segment_brightness=True`, and
  each of the 336 the table admitted before the split (261 by default, 75 behind `allow_modelled`) but
  H61D2-H61D6 resolves to the same profile, field by field, at every pact its rows list, at (99, 99) and at
  none, with and without `allow_modelled`. H61D2-H61D6 change: `SEGMENTS`, `whole_device_kelvin`,
  `segment_brightness=False`, and admitted by default; their whole-device frames are as before.
- **H705A / H705B / H705C / H801B (h705a 125) and H705D / H705E / H705F / H805A / H805B / H805C / H805D (240),
  admitted by default by the live write count (Added): the colour read-back walks `floor(IC / 4)` pieces**,
  the app's (`Support.b`, `ColorMode.Y`), where their rows rounded up: at an IC count that is not a multiple of
  4 it placed one piece more, and read one aa a5 group more where `floor(IC / 4)` is a multiple of 4. The rule
  changes no other SKU's profile: each resolves to the same one, field by
  field, at every pact its rows list, at (99, 99) and at none, with and without `allow_modelled`. Not
  hardware-verified: none is on the bench.
- **H6671 / H6672 (rgbiclight 332) size their pieces by the status read's TLV 0x40, as the app does**
  (`ic_pieces {0, 14, 1}`, where the row fixed 14): `min(IC, 14)` (`ColorPieceConfig.b` case 332, reached
  through `ColorPieceConfig.j` and `.c`), over the IC `VM4LightH667172$afterConnectedSingleReadDeviceInfo$1.o`
  stores from each TLV 0x40 (`getSignedShort`, `Info4BleIotDevice.G0`) and reads back at each TLV 0xA5. Frames
  change only on a strip cut below 14 ICs: its whole-device mask carries its IC's bits, where it carried 14, a
  segment index at or past its IC is refused, and its read-back places the colour groups over its
  IC pieces. An uncut strip (44 / 56 ICs,
  `AcCutCaliH667172.l2`) sends and reads the same bytes as before. `Device.segment_count` follows the device
  from the first poll (14 before it), and a colour write before the first poll runs the status read first,
  sending nothing without a TLV 0x40 count, as on H3401. The profile's `color_readback_live_ic`,
  `color_writes_live_ic` and `color_readback_ic_range` (0, 14) change; nothing else does. Source-traced, not
  live-verified: neither SKU is on the bench.
- **H7025 / H8025 (h7020 296) and H7045 / H7046 (h7045 338) read a TLV 0x40 with the unsigned reader their
  screens use** (`f_40: one_count_u16`, `SegmentSettingController.Companion.b`; their status decode said the
  signed one). Neither sizes anything by it (296 doesn't request it), so no frame, read or decoded state
  changes; only the profiles' `status_decode` does. Every other SKU the table had before the status rows' IC
  reads resolves to the same profile, field by field, at every pact its rows list, at (99, 99) and at none, with
  and without `allow_modelled`, but H3401 and H3510 / H3511 (Added) and H6671 / H6672 (above).
- For integrators: on a `DeviceProfile.color_writes_live_ic` profile (H6641 and every other IC-sized row,
  Fixed below) `Device.segment_count` follows the device from the first poll on, where it was the profile's
  static count, and the library refuses a segment index at or past it. hass-govee-ble-local builds one segment
  entity per `profile.segments`; it needs to size them from `device.segment_count` after the first refresh
  (and reload when the count changes): until then a strip shorter than the profile's count shows entities
  whose writes raise GoveeBleInvalidValue, and H6641's `segment_14` / `segment_15` entities (its count is now
  at most 14) need removing. Release order, as for the card rows (DECISIONS.md 16, "Integration support first"):
  - first, an integration release that sizes the segment entities from `device.segment_count` after the first
    refresh, reloads when it changes and removes the entities past it. It ships dormant for these profiles on its
    current library floor, where their `segment_count` is the static count (`profile.segments` as the fallback on
    a library without `segment_count`), so it builds the same entities there;
  - then the library release that makes these profiles' `segment_count` follow the device (this one);
  - then an integration release that raises its floor to that library release.

  Every other SKU resolves to the same profile, field by field, at every pact its rows list, at (99, 99) and at
  none, with and without `allow_modelled`.

### Fixed
- **A scene firmware gate (`DeviceProfile.scene_versions_by_firmware`) compares versions as the app's
  `BleUtil.parseVersion` does** (`BleUtil.java:1112-1121`; `UtilSku.parseVersion`, `NumberUtil.parseVersion` and
  `BleUtils.q0` are the same function): the digits with the dots removed, as one int, 0 when empty or
  unparsable, where it compared field by field. The two orders differ on a version whose fields are not two
  digits: `1.6.0` (160) is below `1.06.00` (10600), and `2.1.10` (2110) below `1.00.02` (10002). No profile the
  library ships has such a gate (the dreamcolorlightv1 part-brightness gates wait for their scene catalogs), so
  no device changes; a hand-built profile with one does. The spec's reference resolver
  (`govee_reference.client_row`) and the profile's gate check follow, and one parse,
  `devices._resolve.parse_version`, now serves every version compare (the numeric Kelvin compare, Added, too).
- **H60A6 at pact (1,1) sends the app's whole-light 0x0d colour and Kelvin, and no longer offers segments or zone
  colour and brightness (hass #30).** An H60A6 that advertises the app's older protocol, pact (1,1), got the
  (1, >= 2) profile: 13 segments, the zones' colour and the 0x15 colour forms. The reporter's capture (soft 1.00.24,
  hard 1.04.03) shows the device acks those writes (`33 05 00`) and ignores the colour and Kelvin ones: TLV 0x05
  stays on the scene after every RGB and Kelvin write. `profile_for("H60A6", pact=(1, 1))` is now a profile of its
  own, from the row's new pact route (Spec, below).
  - **What it sends at (1,1):** RGB `33 05 0d R G B 00…` (`Bytes.f`), a Kelvin `33 05 0d <tint> <K> <tint>`
    (`Bytes.p`), the h6006 frames, and an RGB pick that is a Kelvin tint as that Kelvin frame. Brightness
    `33 04`, power `33 01`, zone power `33 30`, the scenes and the status read `ac 03 02 41 30` are as before. No
    0x15 frame goes out.
  - **What it no longer offers at (1,1):** `SEGMENTS` (0 segments), so `set_segment_rgb`,
    `set_segment_brightness` and `set_segment_color_temp` raise `GoveeBleNotSupported` before any I/O. The zones
    `main` and `background` keep their `power_index` 0 / 1, with no segments, so `set_zone_rgb` and
    `set_zone_color_temp` raise before any I/O too, and `set_zone_power` works as before. The status read's
    0xA5 colour-group decode goes with them (the read asks for none at (1,1), as before).
  - **The app's path:** `Model.t` opens `VM4LightH60A6` at every pact, but at (1,1) the colour page is the
    default one-piece page (`NewDetailConfig.h`: `ColorModeH60A6` only where `Support.supportPartColor4H60A6`
    holds, code >= 2), and its writes take colour instruction `Config.x[303]` = `Config.kw` = 2 ->
    `Config.J[2]` = `Config.bu` -> `Bytes.f` / `Bytes.p` (`Config.java:14902-14905`, `:5537-5541`,
    `:13459-13467`). `Config.iQ` drops segment brightness there and `Config.AU` is -1, and the header's zone
    toggles stay `33 30` (`VM4LightH60A6.y5:1373-1383`). The app's segment and zone brightness at (1,1) are
    inferred from those tables, not shown by the capture.
  - **The Kelvin path:** the app has two at (1,1): the colour tab's `Bytes.p` and the illumination mode's slider
    (`View4IlluminationMode.I0` -> `KelvinConfig.b` -> `ColorModeControllerConfig.h` -> `Config.N[2]` =
    `Config.GM` -> `Bytes.A`: `33 05 0d ff ff ff <K> <tint>`, then `33 04`). The library sends the colour tab's,
    the coordinator's pick following the H6188 precedent (devlist-traces DECISIONS.md item 3), not a decision of
    the user's; the row's doc records both.
  - **What stays as it is:** H60A6 at (1,2) / (1,3); an H60A6 with no pact heard (no advertisement, or none with a
    pact), which keeps the row's 13-segment 0x15 profile, the 1.x fallback; a pact no row lists (the 1.x pact
    fallback of the 18 SKUs 1.4.0 shipped, `PACT_FALLBACK_SKUS`, unchanged); and every other SKU. A profile, frame
    and refusal diff against master over every admitted SKU (with and without `allow_modelled`, at each listed
    pact and with none) changes H60A6 at (1,1) only. A `Device` built before its pact was heard keeps its profile
    when it then hears (1,1), and logs the existing warning that its profile's rows don't accept that pact
    (`refuses_pact`; `reroute` doesn't follow it, as the two profiles' capabilities and zones differ); the next
    `create_device` from the advertisement routes it.
  - **Evidence:** source-traced, not live-verified: there is no (1,1) unit on the bench. The reporter will test a
    build. This is the user's decision on hass #30 (match the app). It replaces the divergence the firmware
    session kept at (1,1) as a default (D3); the earlier G8 finding and its verification (batch 6) proposed the
    same fix.
- **A failed read of the BGC-info characteristic (2b12) now fails that connect attempt, and no frame is sent on
  its link.** Until now a lookup error (`client.services` raising) or a `BleakError` from the read ran the session
  on the advertised mode. On a device that reports BGC 1 but advertises plaintext, that session sent plaintext
  frames an encrypted device ignores. The attempt now raises `GoveeBleConnectionError` ("BGC read failed" or
  "BGC lookup failed"), the link is torn down, and the session loop reads again on a new link, up to
  `SESSION_ATTEMPTS`. A link-level error from the read (`EOFError`, `BrokenPipeError`) already failed the attempt;
  it now carries the same message. A read that times out raises `GoveeBleTimeout` ("BGC read timed out") and is
  retried the same way. Nothing is cached from a failed read, so the next connect reads again.
  - **What the app does:** it sends nothing on a link whose 2b12 read failed. `BgcInfoReader.a` waits for a read
    callback even when the read didn't start, and `BgcInfoReaderPool.checkNeedParseInfo` takes only a status-0
    read, so `EncryptionBluetoothGattCallback.r`, which starts the handshake or the plaintext session, never runs.
    A device with no 2b12 is unchanged: its session uses the advertised mode, as in the app.
  - **Kept, by decision:** the result is still read once per Device and cached. The app reads 2b12 on every connect
    (`BgcInfoReaderPool.onConnectedInfoChange` resets the reader on connect and drops it on disconnect). So a
    firmware update that changes the version is seen by the next Device, after a Home Assistant reload or restart.
    The AES-GCM refusal without a link, once a read gives 2, stays.
  - **Bench, 2026-10-21, reads only.** No device in range has 2b12 under 1910. A connect-only survey covered 14:
    four H60A6s on SW1.00.41, both H6008s on SW1.01.25, two H6006s, the H6047, H6052, H61A8, H61C3, H6641 and
    H5083. So a real 2b12 read, and a real read failure, are covered by unit tests only.
    - With 2b12 absent, the H60A6 and the H6008 ran two sessions each on the advertised AES-RC4 with every read
      answered.
    - With one lookup error injected on the H60A6's first link, that link carried no frame. The second link
      handshook and its reads were answered.
  - Design: `govee-apks/library-audit/DESIGN_bgc_reread_per_connect.md`.
- **The passive on/off no longer freezes on a stale entry of a merged advertisement record, and the pact
  is no longer taken from one.** `Device.ingest_advertisement` read the on/off and the pact from the first Govee
  entry of `manufacturer_data`. Home Assistant's record
  keeps every manufacturer-data key it has heard for the address. habluetooth merges `{**prev, **new}`, as BlueZ
  does: a key keeps its first slot and holds the value of the last frame that carried it. The Govee header, with
  its encrypt bit and version, is the key's low byte. So after a header change (an OTA), the first entry is the
  old header's, frozen at its last value. Each later change of the new header's entry made the library read the
  frozen one again, and the advertised on/off stopped following the device for as long as the record held the
  old key. habluetooth expires a record only when its address has been silent for 195 s, so the stale key stays
  while the device keeps advertising, and it restores the record after a restart. Another device's Govee frame
  merged under the address leaves the same kind of entry.
  - **The on/off** now comes from the entry whose value last changed between two advertisements the Device was
    given, through `advertised_on` (Added, above). Only a frame that carries a key changes that key's value. A key
    new in the record doesn't count, whether it is another device's frame merged once, a key another scanner's
    record brings back, or an OTA's new header, which counts from its first change. On the modelled images the
    switch byte is the only state byte that changes. Before any change is seen, the first Govee entry with a
    switch byte is read, as before.
  - **Known limitation:** another device's Govee frame filed under the address twice, with different values,
    does take over. Its entry then changed last, and the device's own identical frames fire no callback, so its
    on/off holds until the device's own power next changes. That needs the proxy-path mis-attribution
    (habluetooth#645). The frames seen so far were non-Govee, which are ignored. Clearing the address's
    advertisement history in Home Assistant restores the device's own on/off only until the foreign key is filed
    again: the Device keeps that key as its latest. A lasting cure also needs the Device rebuilt after the clear.
  - **The pact** is now learned only when every Govee pact entry carries the same one, as the encrypt flag is.
    A record whose entries disagree keeps the pact and logs one WARNING per spell, naming the pacts and the one
    kept. The constructor and `create_device` still route by the first entry's pact.
  - **What the app does:** it reads each broadcast on its own. The device card's handler (`AbsBleItem.B`) passes
    one scan record to `AbsBaseBleModel.y` (the pact, `GoodsType.parseBleBroadcastPactInfo` ->
    `BleUtil.parseBleBroadcastPact`) and to `AbsBaseBleModel.z` (the on/off, `BleUtil.parseBleBroadOnOff`). Each
    parser takes the first Govee AD of that record. A record of one broadcast has one Govee key on every modelled
    image, so single-key records read as before.
  - **Bench** 2026-10-22: two passive scans from the Mac, with no connection and no writes.
    - Between them they heard 14 Govee devices in the Mac's range: H60A6 x4, H6008 x2, H6006 x2, H6047, H6052,
      H61A8, H61C3, H6641 and H5083. Neither heard the bench's other plugs (H5080, H5082, H5085, H5089, H5160,
      H5161). The second run's files replaced the first's.
    - Every record held one Govee key, and every device read its real records as the one-broadcast parsers do.
    - Merged records built from the real entry of each of the 13 whose entry has a switch byte read as designed,
      and nothing warned. The H6052's entry has none.
    - The merged records themselves are source-modelled: no bench device can be made to change its header.
  - Design: `govee-apks/library-audit/DESIGN_merged_record_pact_onoff.md`.
- Docstrings cited the app's per-model advert decode as `AbsBaseBleModel.A`, h5080 `Model.A` and
  `Model4Plug2.A`; in the 7.7.02 decompile the method is `z`.
- **H6188: colour temperature now sends the colour tab's frame** `33 05 0d <tint> <K> <tint>`
  (`Config.bu -> Bytes.p`, scheme `h6006`) instead of the white-slot `33 05 0d ff ff ff <K> <tint>`
  (`Config.GM -> Bytes.A`), which the app sends this SKU only from group control and automations. Its control
  screen has no illumination tab (the bundled mode list has no tab 13), so the colour tab's Kelvin palette is
  its only colour-temperature control. Only bytes 3-5 and the checksum of the `set_color_temp` /
  `turn_on(kelvin=)` frame change; RGB, the RGB pick of a tint colour, and the decoded state are as before.
  `DeviceProfile.color_scheme` for H6188 is now `"h6006"`. The `"h6188"` scheme keeps its name and frame for
  H8015 (admitted by default since its pair, Changed), whose illumination tab sends it. Source-traced (Govee
  Home 7.7.02), not live-verified: there is no H6188 on the bench.
- **H6061 whole-device RGB is now the app's one frame** (the user's decision, 2026-10-21: "Keep it, fix the
  frame"). The `h6061` scheme sent a colour as `ceil(n / 4)` `33 05 15 04 <group> RGB x4` frames (`Bytes.m`),
  which the app sends only for colour stripes (`ColorViewModel.O` -> `ColorModeControllerConfig.k`). The
  colour screen's pick goes `DelegateColorV2.F0` -> `ColorViewModel.p0` -> `ColorModeControllerConfig.c` ->
  `Config.J[5]` = `Config.Zt` -> `Bytes.l`: one `33 05 15 01 R G B 00 00 <mask>` frame, the spec's
  `op15_color` kelvin form with Kelvin 0, over the same panel mask as the colour-temperature frame (H6061:
  `ff ff 0f`, its 20 panels, Changed). `set_rgb` and `turn_on(rgb=)` now send that frame; colour temperature,
  an RGB pick that is a Kelvin tint colour, and every other H6061 frame are unchanged. At an explicit one-panel count
  (`Device.set_segment_count(1)`) the RGB frame is the app's too, mask `01 00` (the app writes
  `max(layout, 1)` panels, `CubeColorModeV2.realMyColorPiece`); the colour-temperature frame there still
  writes every panel, as in 1.6.1, where the app's `Bytes.u` writes the one: a known divergence, left for the
  user to decide, that no built-in profile reaches. `build.color_rgb` now builds the `h6061` frame (it raised
  GoveeBleNotSupported) and takes `pieces=` as `color_temp` does; `build.color_rgb_frames` returns one frame
  on every scheme. Source-modelled: no H6061 is on the bench, so this is checked against the app's code only.
- **H6641 and the other admitted h61d3 IC-sized rows write the app's live mask** (the user's decision 2,
  2026-10-21): **H6641** (247), **H6640** (242), **H61B0 / H61B3 / H61B6** (264), **H1A43 / H1A44** (367),
  **H1B6A** (384), **H1AB1** (387), **H1AB2** (388), **H1AB3** (389), **H1A42** (400), **H1AA2** (403) and
  **H1AA5** (404). Each wrote its row's static count, where the app writes the live pieces (above): H6641 sent
  `ff ff` (16) where a 70-IC strip's app frame is `ff 3f` (`ceil(clamp(IC, 41, 70) / 5)` = 14), and the others
  their rule's cap where a shorter strip has fewer. Their whole-device RGB, colour-temperature and tint-pick
  frames and their segment frames now carry the live count's mask; a segment index at or past it is refused;
  and a colour write before the first poll reads aa 40 first (GoveeBleError, nothing sent, without a usable
  count). A unit at its rule's cap sends the bytes it sent before, but H6641, whose old 16 was above its cap:
  its `DeviceProfile.segments` is now 14, the most its rule gives (`H61D3Support.s` / `e`), where it was 16, a
  library bound. H61E5 / H61E6 (239 / 246, the same screen) take the live mask too; they are `app_traced` with
  their segment-brightness gate (`ColorMode.canClickBrightness`) named (Added). The reads are unchanged: one aa 40 per poll, in its place. Source-traced,
  not live-verified: the visible check on the bench H6641 (one whole-device colour with the live mask) is to
  follow.
- **H6098 / H8098 (pact_h6099 203) and H6690 (336): segment brightness now covers the app's groups.** Their
  screen's colour strip, the `ColorMode14Rect4ReverseSelect` that pact_h6099's `DetailConfig` builds for 191 /
  203 / 327 / 336, widens any piece selection to whole groups {0-2}, {3-6}, {7-9} and {10-13} before the
  `33 05 15 02` write (`ColorMode14Rect4ReverseSelect.canClickBrightness` -> `DelegateColorV2Part.U` ->
  `DelegateColorV2.Z0` -> `ColorViewModel.m0`, which also folds the brightness over the widened set). 1.6.1
  sent the selection's own bits: `set_segment_brightness([0], ...)` sent mask `01 00` where the app sends
  `07 00`, and `[3]` sent `08 00` where the app sends `78 00`. The library now sends the app's mask and sets
  that brightness on every segment of the widened set in `state.segments`. So in Home Assistant a brightness
  change on one segment entity also moves the other segments of its group, as it does in the app. Only the
  `15 02` frame's mask bytes and checksum change; every other frame, read and decode is as before.
  Source-traced (Govee Home 7.7.02), not live-verified: none of these SKUs is on the bench. A follow-up, not
  changed here: an empty selection still sends a zero-mask frame (`33 05 15 02 [pct] 00 00`), on these SKUs
  and every other segment-brightness profile, where the app's strips show a toast and send nothing
  (`canClickBrightness` returns null for an empty set:
  `ColorMode14Rect4ReverseSelect.canClickBrightness:380-383` via `e0:480-482`,
  `H605CColorMode.canClickBrightness:497-500`, and the H60A6's `ColorModeH60A6.canClickBrightness:484-487` via
  `X:399-401`).
- **A refused `33 b2` check sent on a live session ends that session, and an operation that overlaps the check
  waits for its outcome.** On an account-locked profile, a secret that `set_secret()` changes while a session is
  up goes out before the next operation's own frames (`_send_pending_unlock`). When the device refused it, that
  operation raised GoveeBleAuthError, but the link stayed up with the check already taken, so the next
  operation's frames went out on it unchecked; and an operation started while the check was on the link skipped
  it as taken and queued its frames behind it, so they went out too before anything ended. Now the refusal ends
  the session first, as a refusal at session setup does (`GoveeConnection._connect_locked`), and an operation
  that overlaps a check in flight waits for its outcome (`asyncio.gather` included): after a refusal it connects
  again, and its own setup sends the check and is refused the same way, so no frame goes out on a link whose
  check failed. The empty check after `read_secret()` (Added) goes the same way. With an accepted check, the
  overlapping operations' frames follow it in the order they were called, where a later one could go first.
  No frame changes for an operation that overlaps nothing. Source-modelled, not live-verified.

### Spec
- **A pact branch can give its pact a profile of its own (h60ax 303 at (1,1), hass #30).** `clientBranch` takes
  `color_scheme` and `zones` (`devices.schema.json`; `tools/gen_devices.py` KEEP; `_resolve.Branch`). A branch on
  `color_scheme`, `zones` or `capabilities` makes its pact a route (`_resolve.pact_routes`): the resolver resolves
  and checks the row with the branch's leaves, as `govee_reference.client_row` applies them, and with the wire
  layer's `by_pact` branch at that pact over its layer. The route is taken at that pact only, and only on an
  advertised pact (`Route.needs_pact`); the row's own route serves its other pacts and an advertisement
  without one. Where the branch drops `segments`, the row's `segment_color_temp` and `segment_brightness_groups`
  are left out. A wire `by_pact` branch at any other pact still raises, and so does a pact route on a row whose
  pacts are `any`. The two pins of D3's unread (1,1) branches, `_resolve._UNREAD_WIRE_BRANCHES` and
  `_UNREAD_PACT_CAPABILITIES`, are gone (H60A6's were their only entries). `profile_for`'s 1.x pact fallback
  routes a SKU as it routes a pact-less advertisement, so a pact route is never the fallback.
- **A zone may have no segments** (`clientRow.zones[].segments`, `minItems` 0): a zone the screen only switches on
  and off, with no zone colour or brightness.
- **h60ax 303 `by_pact "1,1"`**: `color_scheme: h6006`, the zones `main` (power_index 0) and `background` (1) with
  no segments, and the recorded capabilities `[power, brightness, rgb, color_temp, scenes]` and status_read
  `[cmd_41, cmd_30]`. Its wire branch keeps `num_pieces: 1` and adds `counts: { num_segments: 0 }` (`Config.bu`
  ignores posSet, so the 0x0d frames carry no mask). The row's doc replaces the "Divergence kept (decision D3)"
  text with the app path (both Kelvin paths, CLAUDE.md rule 5), the reporter's observation and the earlier work
  (`LIBRARY_DIVERGENCES_dd7c53e_SUMMARY.md:146-157`, `VERIFICATION_af77174.md` batch 6, `:259-290`), and cites
  the new path in `doc-ref`.
- **The device-list trace (Govee Home 7.7.02; 7.5.12 where a family says so).** Each of the 83 census families
  was traced as the app runs it: the device-list model, the detail screen, its view model, then the builders,
  with `Class.method` and line citations in each row's `doc` and `doc-ref`. Core control decided a row's
  support: routing and pacts, power (zones and the global switch), brightness, RGB, colour temperature,
  segment colour and brightness, and the session reads, keep-alive and read-back they need. Scenes, music,
  DIY, timers and other extras were left out and never blocked a row (`scene_dialect: untraced`). A row whose
  core control the library serves as the app does is `app_traced`; one the library still serves differently is
  `modelled`; one that needs library work has no `support` and names the gap in its `doc`. A verification
  pass challenged each family's rows; they were repaired, and demoted (to `modelled`, or to no `support`)
  where a core claim didn't hold.
  - devices.yaml now has 305 goodsType rows in 96 families (184 in 91): 226 with `support` (66), of them 212
    `app_traced`, 10 `modelled` and the same 4 `hardware_verified`, and 79 without (118). 66 of the 226 had support
    at 1.6.1 and keep it (bulblightv3 328 now `app_traced`); the trace gave 87 more rows support. The other 73 take
    it through the library and spec changes under Added, Changed, Fixed and Spec: h7004 0, h6069 284, h3700 383,
    h7076 313, h70dx 267-269, bulblightv3 342, the four h1162 rows, h70b1 134, h70bx 216, the six status-read rows
    (h6092 334 / 333 / 401, h7072 371 / 294, h70b6 341), the nine BK target rows, the five Glide panel rows (h6061
    96 / 97 / 121 / 163 / 228), the seven `legacy_02` rows (h6105, h6107, h6129, h6185, h73101217, h7318 and h6165,
    each goodsType 0), h60ax 235 and h61d3 239 / 246 (the colour-temperature work, which also promoted h60ax 302 to
    `app_traced`), h7050 98 (H7061's two heads; the aa 40 section sum promoted dreamcolorlightv1 204 to
    `app_traced`) and the three device-list card rows (h70b2 185, h6630 285 / 286), h6092 283 (H7070's exact-pact
    status read), h6071 57 / 67 (the `kelvin_v9` scheme), stringlightv2 40 / 108 (the exact-pact pairing write),
    h6057 104 (the light switch), h61d3 375 and h6609 173 (the IC piece rules), h705a 180 (the special-info read),
    h7020 316 / 318 / 337 / 391 (the status rows' IC reads), h7020 411 (the 0xA3 colour unit), the eight h7020
    bulb-string rows (53 / 54 / 62 / 63 / 100 / 102 / 149 / 161, the aa 0f string count), h707a 326 (the TLV 0xA1
    segment count) and dreamcolorlightv1 71 / 123 (narrowed, with the per-SKU connect reads), all `app_traced` but
    h6119 44, h6609 173 and h705a 180 (`modelled`); the user's decision 27 promoted the Glide h6061 228 at the 20
    panels its colour page drives (Added), the numeric Kelvin compare h60ax 235 (Added), the user's decisions on parity promoted h7072 294, h61d3 239 / 246 and the trace's h7090 44
    (below), and the empty secret check the trace's hollowlamp 59 (Added). The segment split promoted the
    trace's h61d3 202, the live piece count h705a 125 / 240 / 250 and h61d3 299 / 261, the status rows' IC reads h705a 394 and h7057 349, and the
    read-back piece count h6609 260 (five of its SKUs held `modelled` by their own `support`), to `app_traced`,
    and the colour page's Kelvin-tint read-back the trace's h70b3 229 / 230 / 231 / 255 / 256; the user's
    decision promoted its h604a 109 / 133 (below).
  - **h7090 44 (H7090), h7072 294 (H7072) and h61d3 239 / 246 (H61E5 / H61E6) are `app_traced` on parity with
    admitted rows that share their one gap**, each gap still named in the row's doc (the user's decision 25: D5
    and D2 (a), 2026-10-22). No fact or frame changed; the promotion only admits them by default:
    - H7090 runs H7092's screen code (chassislightv1 162, h7090 `NewDetailActivity` / `VM4Light`): after a CCT
      report `VM4Light.i5` keeps the tint fill as every segment's colour and takes only each aa a5 record's
      brightness, where the library stores the walk's RGB. H7092's doc now names it too. The fold is a per-row
      flag, a later release.
    - H7072 shares H3351's screen (`H7072VM4Light`): the app places each TLV 0xA5 group at (group - 1) x that
      TLV's own record count (`SubMode4Color.j`), the library at (group - 1) x 4 (`status_a5_stride_per_tlv`),
      a gap the admitted H6860 / H60B1 status rows share.
    - H61E5 / H61E6: `ColorMode.canClickBrightness` refuses segment brightness while a selected piece shows a
      Kelvin tint colour; the library doesn't gate it, nor on the admitted rows whose strips do the same
      (straightfloorlamp 69 / 377, dreamcolorlightv1 193, h7078 245, h6047 119:
      `ColorMode10Segment.canClickBrightness:570-579`). The gate is the next release (D2 (b)).
  - **A pact branch's `support`** (clientBranch; devices.schema.json, `tools/gen_devices.py` KEEP,
    `_resolve.pact_support`): the row's support level at that pact alone, for a row whose per-unit gates take the
    stated branch only there. The resolver routes each SKU at each pact at its level and, without a pact, at the
    row's own, so a route only a branch admits needs the advertised pact (`Route.needs_pact`); it refuses one on a
    row whose pacts are `any` or beside a `by_model` support. dreamcolorlightv2 21 is `modelled` with `app_traced`
    at (2,1) (Added); the reference resolver's `client_row` gives a branch's support at its pact.
  - **`empty_secret_check`** (clientRow, `const` true, only with `account_lock: true`; devices.schema.json,
    `tools/gen_devices.py` KEEP): the screen sends its 0xB2 check on every connect, empty when the account holds no
    secret. Set on hollowlamp 59 and h6071 57 / 67, whose docs cite the connect lists, say the library sends the
    empty check and name the pairing screen's read that sends none (`AbsPairAc4SecretV1.T1:107-113`); absent on
    the other `account_lock` rows (hollowlamp 27 / 37, `H605051NewDetailVm:163-169`, and the plugs).
    `govee_ble_frame` `secret_write` documents the empty check (`Controller4Secret.Companion.a("")`,
    `Encode.decryByBase64`, `BleUtils.p`'s zero fill: the 8 secret bytes zero).
  - Two goodsType add-ons were traced and checked on their own: straightfloorlamp 69 (H6076 / H8076,
    `app_traced`, with the bench reads above, on an advertised pact only: `pact_required`, below) and
    rgbiclight 141 (H61C2 / H61C3 / H61C5).
  - h604a 109 / 133 (H604B / H604D) are `app_traced` on their Govee Home 7.5.12 trace, the user's decision 25
    (D3 (a), 2026-10-22). 7.5.12 has the h604a screen in its base APK, while 7.7.02 runs it from the on-demand
    module `pact_h604a` (`pfd/Config.java:242`), which the 7.7.02 capture doesn't hold, and reads a
    module-registered `LightCapabilitySpec` first (`LightColorCapabilityCenter.i3`), so the shipped path is
    unseen; a re-trace waits on a capture of the module. What the 7.7.02 base shows agrees: its legacy colour,
    white-type and Kelvin maps for 109 / 133, and the keep-alive hook a screen's view model overrides
    (`ShareVM.M3:979-990` passes `K3:1362-1364` = `ConnectHeartbeat.a(I3())` to `BleComm.J:57-59`; true is aa 01),
    which 7.5.12's `H604xVM.i4` sets true. Both rows keep `segment_color_temp: false`: the colour-temperature
    pick covers all 14 pieces (`ColorModeH604x.canClickColorTem`), and 7.7.02's `SupportPartColorTemConfig.d`
    lists neither goodsType. Each row's `doc` says so; `devices.schema.json`'s `docRef` and the `source_app` line
    name `govee-apk-decompiled/sources/` as the 7.5.12 root of their doc-refs.
  - **`pact_required`**, a new `clientRow` fact (`devices.schema.json`; `tools/gen_devices.py` KEEP): the row
    is a route only for an advertisement that carries a pact, as a pact-conditioned reassignment's target row
    is, because the app also accepts other pacts for its goodsType that run a path the row doesn't serve.
    straightfloorlamp 69 and the five h70b3 rows set it (on both screens (1,1) runs the polled connect read),
    and so do h70b6 329 (above (1,1) its status read adds 0x7D / 0x7E) and pact_tvlightv2 24 (at (1,1) its
    colour write is `33 05 0b`, `Bytes.c`). The resolver marks its routes with the one flag that also marks a
    reassignment's target routes, each route of a SKU a pairing write names and a pact route
    (`_resolve.Route.needs_pact`, read by `profile._accepts`), and refuses `pact_required` on a row whose
    pacts are `any`. `govee_reference.ble_rows` skips such a row without a pact (its self-test checks H6076).
  - **`compare`**, a new `clientFirmware` key (`devices.schema.json`; `tools/gen_devices.py` KEEP), on a
    `kelvin_range` item only: how its gate compares the device's version, `string` (the default) or `numeric`
    (`BleUtils.isHigherVersion`). h60ax 235 sets `numeric` (`KelvinConfig.Config.Y2`). The resolver refuses it
    on any other item and refuses a row whose Kelvin items mix compares; a `string` gate keeps the two-digit
    `a.bb.cc` form. The goodsType-keyed `_resolve._NUMERIC_KELVIN_GATES`, which refused a Kelvin branch on 235,
    is gone. `modeColorVersion` now says a version compares as `BleUtil.parseVersion` (Fixed), and
    `govee_reference.client_row` follows both compares (its self-test checks H60A1 at seven hardware versions).
  - h6113 39 (H6113) names colour scheme `h6006`, the colour tab's `Config.J[2] = bu` frames its screen sends,
    not `h6188`, whose white-slot CCT (the illumination tab's `Bytes.A`) the screen never sends. The row has
    no colour temperature, and both schemes send the same RGB and tint-pick bytes, so only H6113's
    `DeviceProfile.color_scheme` changes (a new SKU).
  - **rgbiclight 141 (H61C2 / H61C3 / H61C5) is `app_traced`, admitted by default** (pacts (1,1) / (2,1)): power,
    brightness, RGB, colour temperature [2000, 9000], 15 segments and `15 02` segment brightness. Its one
    device-reported fact, the aa 40 section sum that sizes the app's connect aa a5 walk (`VM4Light.N5`), was
    read as 0 on the bench H61C3 (`BENCH_20261021_h6076_h61c3.md`), so the app walks the row's 15 pieces; the
    user accepted that reading for H61C2 / H61C5, which were not on the bench (the library now sizes the walk
    from the live sum, as the app does: the section-sum entry under Added). Left out: gradual (the app reads
    no aa a3 here), the `33 09` time sync and scenes (no catalog). The library sends the app's connect reads in
    its own fixed poll order: aa 05 before aa 40, and the device-info reads last rather than first. The row's
    `doc` records this. Control is not hardware-verified.
  - Nothing new is hardware-verified. The only bench evidence is the read-only H6076 / H61C3 check above.
  - Spec corrections the trace made inside family blocks change no admitted SKU. For example, H6171 is off
    barelightv1 17 (it reads aa 40 there, which the row's other SKUs don't), H6119 is off h7090 44 (h7090's
    screen never opens for it), and H615E is listed on stringlightv2 108, its catalogue goodsType.
  - The catalogue snapshot routes 41 SKUs no row lists (15 before), H8069 and H80D1 among them, and H30D1,
    H7028, H61B1 and H6172 / H6173 / H618E / H618F / H61B2 on the h7020 and dreamcolorlightv1 rows written out
    since the trace. For review: H613A-F (catalogue 39, named by h613839's (65535,255) bind item), where it was
    H615E.
  - rgbiclight 351 / 352 now record the R617G / R618D catalogue route, as h7057 274 does H8057's.
- **`segment_brightness_groups`, a clientRow fact** (devices.schema.json): the disjoint groups the colour
  page's segment-brightness slider widens a selection to before the `33 05 15 02` write (Bytes.G), which
  becomes `DeviceProfile.segment_brightness_groups`. The resolver serves it only on a row with that frame
  (`segments` on an `h60a6` row), as disjoint groups of the row's segments. `govee_ble_frame`
  `op15_brightness` now says its mask is the strip's position set, which some strips widen. Nine admitted rows
  carry {0-2} / {3-6} / {7-9} / {10-13}, each traced to the strip its screen builds: pact_h605b 120
  (`H605CColorMode`, from `DetailConfig`), 142 / 243 (`ColorMode14Rect4ReverseSelect`, from
  `HdmiDetailConfig`) and 360 (the same strip, from `DetailConfigH2A40`), and pact_h6099 191 / 203 / 327 / 336
  (the same strip, from `DetailConfig`) and 273 (the H6097 `ColorMode`, from `H6097DetailConfig`). Each row
  cites its strip's whole `canClickBrightness`, through the return that hands the widened set on (H605C:
  `H605CColorMode.java:496-539`, which had stopped at :536, short of the return at :538). The row docs that
  called these strips' frames byte-identical to the h60a6 scheme (the pact_h6099 `color_scheme` note) or left
  the widening out now say that the `15 02` mask is the widened set. The held rows whose strips widen too
  (pact_h605b 82 / 138 / 172 / 192, pact_h6099 253, h6609 173) keep no groups until they are admitted. The
  H60A6's strip (h60a6 303, released) widens too, but by colour state rather than by fixed groups, so its row
  records it in the doc only and the library is unchanged: `ColorModeH60A6.canClickBrightness:483-500` sends
  the selection as it is, except on pact (1, >= 3) (`Support.supportPartTem`) while a ring piece is selected
  and the 12 ring pieces show one Kelvin-table colour (`ColorModeH60A6.F` / `G`), when the mask covers the
  whole ring 0-11 with piece 12 as picked. Modelling it is the user's decision. The Part-tab Kelvin of the
  H60A6 and the H6047 (h6047 119, released) is now recorded in both rows' `doc`: the app widens it to the
  whole ring once a ring piece is selected (`ColorModeH60A6.canClickColorTem` over `F`) and to each bar
  touched (`ColorMode10Segment.canClickColorTem` with `q0` / `r0`), where 1.6.1's `set_segment_color_temp`
  sent the selection as it is. Both rows now say `segment_color_temp: false` (decision 24, under Changed); the
  zones keep their Kelvin and the segments their brightness.
- **Screen facts on three held rows** (still without `support`; no SKU's profile changes):
  - h61d3 375 (H1A45): its colour config is traced after all. The 7.7.02 tables key 375 by the named
    constant `LightRecycleViewGesture.W`, which a search for the number missed. It is row 367's: the h60a6
    `15 01` frames, gradual `33 a3`, `15 02` segment brightness and Kelvin [1000, 10000]. Only the two-IC
    piece count holds it.
  - h6160 19 (H6163 / H6117) and h6127 17 (H6116 / H6127 / H6161): 15 pieces each (the bytecode of
    `ColorPieceConfig.n`, whose decompile is broken), the aa a5 / aa a2 read-back gate, and the gradual and
    segment-brightness flags; h6160 19 also gets its aa 01 power read and keep-alive. Both stay held, and not
    by their firmware-gated routes alone:
    - h6160 19 is also held by the family's H6163 / H6117 `by_model` entries. They are the old-hardware
      (row 0) decode, a `mode_color_parse` with a firmware branch, but `by_model` applies family-wide, so it
      reaches row 19 too. It has to move to row 0, or be restructured, before row 19 can take support.
    - h6127 17 is also held by its (1,1) / (1,2) `by_pact` connect reads (aa 14, aa a2), which the library
      doesn't serve.
    - A scratch stand-in gave each row support and dropped its gate, and also the `by_model` entries (19) or
      the (1,x) pacts and their branches (17). With that, H6163 at (2,1) resolves to H61A8's facts except its
      scenes, its SKU-keyed Kelvin and `mode_reports_kelvin`, and H6116 at (10,1) to 15 segments,
      bulb_batch_v2, gradual and [2000, 9000]. A stand-in that drops only the gates is refused by the
      generator.
- **Doc corrections** (no frame, read or profile changes):
  - `mode_color_parse.kelvin_15`: the library reads the 0x15 Kelvin signed, as the app's screens do (the
    stale "reads it unsigned, gap item 16" note is gone; only the 1.x `rgb_kelvin` default reads unsigned).
  - h7020: the H7015 -> 62, H7016 -> 63 and H3010 -> 411 ties are the catalogue's goodsTypes, as the app's
    scan takes them (`Pact.f`), not inferred from the `addSupportPact` order.
  - straightfloorlamp 69 (H6076 / H8076): the cipher is per device (the advert's 0x40 bit or the BGC read, the
    app's `EncryptionUtils.isEncryptionSupported`), plaintext with neither, as in the app. The firmware
    answers the library's aa 06 / aa 07 03 / aa 07 02 reads (bench 2026-10-21), which the app's screen doesn't
    send (it reads TLV 0x07): a hardware-observed extension (rule 6), now cited as such here and in
    `read_device_info`.
  - `read_device_info` now says where the app sends these reads by connect path, not by SKU. A screen that
    connects with the 0xAC status read (`Compose4BaseInfoSingleRead`, every `readback == "status"` profile)
    sends none of them and takes its versions and device id from TLV 0x07. The library's reads there are
    firmware-sourced (rule 6) only on the H60A6 and H6076, where a firmware image or the bench shows them
    answered, and every other status row sends none (`device_info_reads: none`, under Changed). The old text
    named only the H60A6 and goodsType 69 as exceptions.
  - h705a 184 (H608A-D / H808A): the app sizes its first aa a5 walk from the server's per-SKU IC
    (`AcNewDetail4H608A.e3` drops the account's IC, so `AbsAcLightDetail.K1` takes `SkuIcManager.d`), the
    library from the live aa 40, which the app then settles on itself (`VM4H608A.S4`). The row and
    `walk_mechanism_a_direct` document it as the divergence the user approved (decision 21(a), "Live count
    only"), and the row is `app_traced` (its write mask now takes the live count, Added).
  - straightfloorlamp 69 (H6076 / H8076): the strip refuses per-piece brightness, with a toast and no `15 02`
    frame, while any piece shows a Kelvin-tint colour or none is selected
    (`ColorMode4H6076.canClickBrightness`: `ColorUtils.o`, `NumberUtils.A`); the library doesn't gate
    `set_segment_brightness` on it (not modelled). The Kelvin slider covers all 7 pieces, and a partial-mask
    Kelvin frame comes only from a tint-colour pick (`canClickColor` -> `ColorViewModel.p0` -> `Config.Yt` ->
    `Bytes.r`), as on 377.
  - pact_h605b 142 / 243 (H6602, H6604 / H8604): a tint-colour pick on the selected pieces does send a
    partial-mask Kelvin frame (`ColorMode14Rect4ReverseSelect.canClickColor` returns the selection ->
    `ColorViewModel.p0` -> `Config.Yt` -> `Bytes.r`); only the Kelvin slider is whole-strip. So
    `set_segment_color_temp`'s frame is one the screen sends, not a scoping gap, as the docs said.
  - h6159 40 (H6159 / H6110 / H6109 / H614B / H614E): the app's scan parses no pact for an ihoment_ / Govee_ /
    Minger_ name of these goodsType-0 SKUs (`BaseBleProcessor.c` returns before `parseBleBroadcastPactInfo`),
    so such a unit never runs at 40 in the app; the library reads the advertised pact whatever the name, so a
    legacy-named unit with a (2,1) pact block routes there. Documented as a divergence for review (whether
    such a unit exists is unchecked); the library doesn't route by name prefix. h6113 39 notes the same.
  - h61d3 264: H61B0's IC count is unknown (no catalogue entry, no bench unit); its writes take the live count
    (decision 2's live mask, Fixed), so only the count before the first read, the row's 15, assumes it has at
    least 15, as H61B3 / H61B6 do.
  - h61d3 263 (H703A / H703B) is `modelled` for the screen's unmodelled re-read on a switch change alone: it now
    reads its colour from TLV 0xA5's one record and follows the aa 01 keep-alive (`heartbeat_power`, Added). Its
    doc doesn't give the unasked base TLVs as a reason: they hold on every status row, the `app_traced` ones
    included, and set no row's level.
  - Three doc-ref line ranges past their file's end are corrected: h60ax 302's
    `VM4Light$afterConnectedSingleReadDeviceInfo$1.java` (176 lines), pact_tvlightv3 112's
    `MTURequestManager.java` (54 lines) and bulblightstringv1 3's `ControllerBrightness.java` (76 lines; now
    `:30-75`, `Companion.c` to `getCommandType`). No other doc-ref in `devices.yaml` cites a missing file or a
    line past its end.
- **The status rows with an aa 01 keep-alive state the app's live power.** straightfloorlamp 69, h7020 296 and
  h61d3 263 drop their `[push_30]` override and h7086 293 its `[]`, so each takes its family's list again;
  rgbiclight 351 / 352 and h7020 361 list `[heartbeat_aa01]`. Each screen's `I3` is true (ConnectHeartbeat
  type 1), and `ShareVM.N3` takes the switch from the reply (`J3`). The overrides only worked around the
  resolver gate above. No profile changes.
- **The bare status read.** `devices.schema.json` clientRow `status_read` allows `[]` (`minItems` 0), the
  read `ac 03 00` with no type requested. `govee_ble_frame` `status_read_args.num_types` documents 0 as that
  read (`Compose4BaseInfoSingleRead.Companion.a` passes no ext bytes), and `govee_commands`'
  `build_status_request` self-check covers it.
- **Per-SKU connect writes.** `devices.schema.json` `clientModel` takes `connect_writes` (the row's stays
  required), which `govee_reference.client_row` applies for the SKU. bulblightv3's `by_model` gives H14A1 /
  R14A1 / H14C2 / R14C2 `[light_effect_stop]` over row 342's `[]`.
- **`segment_color_temp`, whether `set_segment_color_temp` may send a Kelvin to a piece selection.**
  `devices.schema.json` clientRow takes the boolean, with the app trace in its description:
  `DelegateColorV2Part.Z0` (:686-704) adds the Part tab's Kelvin item only where `LightColorCapabilityCenter.C2`
  holds. C2 falls back, as no supported goodsType has a capability spec (only fanlight 359 / 368 register one),
  to `SupportPartColorTemConfig.b`: list d, 112 at pact (2,1), H7022 / H7024 at goodsType 0
  (`SupportPartColorTemConfig.java:36-55,71-91`). The All tab's Kelvin covers every piece
  (`DelegateColorV2.G0`). Each of the 118 rows with support that keeps `segments` and `color_temp` after its
  colour scheme's drops now states it, 35 true and 83 false, with that doc-ref. The resolver requires it on
  exactly those rows and refuses it on any other, and refuses true beside a `color_temp_control`, whose Kelvin
  covers every piece. Four list-d rows say false, because their Part-tab Kelvin item widens every pick to
  whole zones: h60ax 303 and h6047 119 by the user's decision 24 (Changed), and the held h60ax 356 / 373
  (H1232 / H1252, behind `allow_modelled`; `ColorModeH1232.canClickColorTem:486-508`,
  `ColorModeH1252.canClickColorTem:562-584`) by its extension (the user's decision 25, D11). Docs
  corrected where they said a non-list-d screen's Kelvin follows the piece selection (pact_h605b 120 / 360,
  h7050 157), or called the missing refusal a library gap (h6047 277). pact_h605b 142 / 243 now say their Part
  tab has no Kelvin item, next to their note that an in-range tint-colour pick on the selected pieces still
  sends a partial-mask Kelvin (`canClickColor` -> `ColorViewModel.p0` -> `Config.Yt` -> `Bytes.r`), the frame
  `set_segment_rgb` sends for a tint colour (`build.rgb_as_kelvin`). h6047 277 now takes its all-pieces Kelvin
  from the All tab (`DelegateColorV2.G0`), not from the strip's `canClickColorTem`, which no Part-tab Kelvin
  item reaches there. h7050 77 / 157 take their colour-page flags from `DelegateColorV2.C1`, not from the older
  `DelegateColor`, which no screen builds, and cite that path.
- **The aa 01 "on when != 0" rules.** `govee_profile` `power_rule` gains `light_nonzero` (the connect read)
  and `light_heartbeat_nonzero` (the aa 01 heartbeat; false = as `light_nonzero`), the light counterparts of
  `relay_on_mask` and `relay_heartbeat_on_mask`. `govee_state` `power_state.is_on` reads a light's reply as on
  for any non-zero byte under `light_nonzero`, and `is_on_heartbeat` also under `light_heartbeat_nonzero`
  (still `== 1` without them; the status TLV and the plug masks are unchanged). devices.schema.json's
  `wirePower` takes both as a row's wire `power`; the resolver serves them on a polled light row only, and
  `light_heartbeat_nonzero` with the aa 01 heartbeat only. `govee_reference.make_profile` takes both, and the
  reference self-test checks the rules on an aa 01 reply and heartbeat. `wire_profile` now gives a non-plug
  profile's power rule masks 0 (a light's), where it passed the unused relay default; no decode changes.
  USING_THE_KSY.md and the `relay_on_mask` doc give the four-param rule.
- **Eight more `govee_common` command ids**, the TLV types the app's 0xAC status reads request: `cmd_0c`
  (the H7070-screen operate info), `cmd_2c` (the H60B0 operate sub-mode), `cmd_45`, `cmd_48`, `cmd_4b`, `cmd_7d` /
  `cmd_7e` (the H70B6 / H70B8 clock and temperature unit) and `cmd_a1` (the H707A segment info). None but
  `cmd_a1` (under `form_a1`, below) gets a typed decode, so their values stay raw in the status stream's `rest`,
  as an unnamed tag's did: no admitted SKU's request or decoded state changes. Only the frame analyzer's labels do (`write/cmd_4b`, where it said
  `write/cmd=0x4b`).
  - h6092 283 / 334 / 333 / 401 read TLV 0x41 as the power-off memory, so a burst whose 0x41 is shorter than
    two bytes applies nothing, and mark an empty TLV 0x05 as the app's failed read (the library reads past that
    one, as on H60A6): their screens read 0x41's byte 1 and 0x05's byte 0 unguarded (`VM4LightH7070.X4`,
    `Info4DetailH7070.H`, `VM4LightH3860.U4`, `Info4DetailH3860.I`). h6092 283 (H7070) now states its status read,
    whose pacts (1,2) and (2,1) add 0x42; it is admitted on the exact-pact status read (Added), and the
    reference self-test checks its read at each pact (`client_row`).
  - `write_check_ic` (0x42) also names the H7073 effect-change switch that 333's status read requests
    (`GET_NECESSARY_INFO` / `controller_type_lamp_effect_change`, `VM4LightH7070.X4`); its value is untyped.
  - A known gap, named in the rows' `doc`: the same handlers read byte 0 of TLV 0x42 (333), 0x45 (401), 0x39
    (h70b6 329 / 341) and 0x7D / 0x7E (341), and H7073's 0x0c layout (`OperateModeInfoH7070.E`), unguarded, so
    an empty or short value fails the app's whole status read, while the library, with no form for them, still
    applies the burst's power and brightness.
- **h6061: the whole-device RGB pick is `Bytes.l`.** The family's new `color_write` opcode records it, with the
  path above; `color_group_write` (`Bytes.m`) is now labelled the colour-stripe path, and `color_temp_write`
  cites the slider's own path (`ColorViewModel.U` -> `c` -> `Zt`). The resolver checks that an `h6061` row with
  RGB or colour temperature carries the `f_15_01: kelvin` form (it checked `f_15_04: rgb_group` for RGB).
  - The family's rows are re-cut from the trace (h6061.md), not from its patch, which withdrew H6061: 96 / 97 /
    121 / 163 / 228 get their own traced rows (above: 96 / 97 / 121 / 163 `app_traced` at a full layout, 228
    `app_traced` at its colour page's 20), and 51 keeps `app_traced`, names the panel count and the user's decisions, and drops `scenes`
    from `capabilities` (an `untraced` dialect claims nothing; the resolver already dropped it, so the profile is
    unchanged).
  - `specs.segments`, `wire.doc`, the `aa a5` read and the per-goodsType opcode (now `color_write_per_goodstype`)
    say what the app sizes from the stored layout L and what the row's count stands in for; the brightness
    opcode cites the screen's `BrightnessConfig.b`, not the device-list `Support.getBrightnessRange`.
  - A full layout is the layout editor's cap for the goodsType's shape (`Constants.o`, by
    `Support.getShapeViewType`; `BasicShapeBusiness.x` / `x0`): the wire `num_segments` of 51 and 97 go from 23,
    `Support.getBulbStringMaxNum`'s read-back default with no layout stored, to 20 (H6061, Changed), and 96 / 121 /
    163 already held their caps, 25 / 20 / 21. 228's layout is the device's own report of up to 21 panels
    (`GraphicalIdentificationActivity.m1`); its 20 is the panels its colour page addresses
    (`CubeColorModeV2$initView$1$7.selectedShape`: `Constants.o(16)`), and a 21st panel is not addressed, as in the
    app; its doc says so.
- **`color_02_write` (govee_ble_frame) is the `legacy_02` scheme's frame**, now citing the writer the library
  follows (`Config.fu` -> `Bytes.a` / `Bytes.o`, from `ColorViewModel.U` / `p0`), and its `white_flag` takes a
  new enum, `color_02_flag` (`rgb` = Bytes.a, `white_tint` = Bytes.o / Bytes.y). `spec/govee_reference.py`
  checks that `govee_commands.build_color_legacy` reads back as both forms.
- **devices.yaml**: `color_scheme: legacy_02` and `support: app_traced` on h6105, h6107, h6129, h6185,
  h73101217, h7318 and h6165 "0"; `specs.kelvin_range: [2000, 9000]` on h73101217, h7318 and h6165 (the range of
  `fu`'s Kelvin-tint test, `KelvinConfig.Config.i[0]` = `P1` = 0; those screens have no colour temperature). The
  resolver now requires a `kelvin_range` on a row with `rgb` under every scheme with a colour-temperature mode
  (all but h61a8), not only under `color_temp`: each of their writers (`Config.Yt` / `bu` / `Zt` / `fu`) runs
  `KelvinConfig.i` on every pick whatever the screen offers, and without the range the library would send a
  tint pick as plain RGB. Every row already has one, so no profile changes. devices.schema.json lists the six
  colour schemes as `color_scheme`'s enum.
- **devices.yaml, the held colour-version-4 rows** (bulblightv3 0 for H6001 below its pact gate, h6159 0, h6160
  0, h6104, h6113 0, h6114 0, h6127 0, h613839 0, h6181 0, h6182 0, chassislightv1 11): their docs no longer say that no library
  scheme builds the `Config.fu` 0x02 frames, which are now `legacy_02`'s. Each names the gaps that still hold it:
  routing below the gate, the `rgb_flag_tint` (h6104: `rgb_flagnz_tint_02`) read-back that `parse.mode_color`
  does not fold, the ffe0 / ffe1 GATT service, the soft-version route, chip-gated brightness, or firmware-gated
  colour. Docs only: no row, value or profile changes. h6101's 0x02 frame stays a gap: it echoes the reported
  tint (`SubModeColor.getWriteBytes`), which `legacy_02` doesn't.
- **`white_type`** (devices.schema.json clientRow; `rgb_black`, `rgb_white`, `rgb_self_tem_color`, absent =
  `rgb_white`): the white type the colour page hands `Bytes.r`, not `Config.P` read raw (the rgbiclight colour
  page passes 1, so goodsType 176, `Config.P` 0, stays `rgb_white`; RGB_SELF builds as `rgb_white`). The
  govee_ble_frame `op15_color` `color` field documents the slot per white type. h61d3 239 / 246 state
  `rgb_self_tem_color`; h705a 180 states `rgb_black` and the `kelvin_tint` form, and stays without support (its
  aa a1 0b piece count and its Kelvin read-back are still gaps). The reference self-test reads a `rgb_black` and
  a partial `rgb_self_tem_color` frame.
- **`color_temp_control`** (devices.schema.json clientRow; `illumination` or `whole_device`, absent = the
  colour page's one-frame write over the selected pieces). `whole_device` needs the color_temp capability; h61d3
  239 / 246 state it (`ColorMode.canClickColorTem` returns every piece for `H61D3Support.i0`). For
  `illumination` the resolver needs the color_temp and brightness capabilities, percent brightness with no pact
  branch, and the h60a6, h6188 or kelvin_v9 scheme (the three whose colour-temperature frame is a `Config.N`
  builder: `Bytes.C`, `Bytes.A`, `Bytes.D`), and then lets the colour page's `color_temperature` flag be
  off. h60ax 302 (with
  `color_functions: [segment_brightness]`, `Config.I[302]` = hQ) states it and is `app_traced`; h60ax 235
  states it too (`modelled`), and so does bulblightv3 328 (H8015), now `app_traced` on the user's sign-off
  (Changed).
- **`color_functions` and segment brightness** (devices.schema.json descriptions): `segment_brightness` needs
  `segments` on a scheme with the `15 02` frame, and a row with segments offers segment brightness only where
  it is listed or `color_functions` is absent. h61d3 202 gets `segments`, `color_temp_control: whole_device`
  and `support: app_traced` (was `modelled`, segments withheld).
- **A pairing item's `pact`** (devices.schema.json `assignedGoodsType`): required on a `bind` item, and documented
  as the one advertised pact the write is made at; the generated table keeps it (`tools/gen_devices.py`), which
  prints the pairing routes for review. stringlightv2 40 / 108 get `support: app_traced` and full rows (`doc`,
  `doc-ref`) at their (2,1) facts, and the reference self-test checks H615E's route at (2,1) and none at
  (65535, 255).
- **`kelvin_v9`** is a `color_scheme` (devices.schema.json's enum and its description), and the spec's
  `op15_kelvin_v9` now cites its selection path (`Config.N[9]` = `JM`, `Config.x[57]` / `[67]`, the colour
  page's `Config.J[9]` = `hu`). h6071's `client_profile` names it, and rows 57 / 67 get `support: app_traced` and
  `color_temp_control: illumination`, citing the bundled mode list `[10, 13]` and `ShareVM.C3` / `Y3`.
- h60ax 235 is `modelled` (above), with `color_functions: [segment_brightness]` (`Config.I[235]` = zN). No
  `.ksy` change: govee_state already reads an aa 05 reply with the profile's `mode` decode, and its empty-value
  rule only for a status TLV.
- **The aa 40 section sum.** `govee_state` `ic_count` gains `segment_2_signed` and `section_sum` (bytes 2 and 5
  as Java bytes, absent where the sectioned IC-pair reader `ControllerIcNum.d` throws, `app_pair_fails`), and
  devices.schema.json's `ic_pieces` gains `sections`. rgbiclight 141 and dreamcolorlightv1 204 take it.
- **Command 0x34 is `cmd_34` (was `swap_light`), overloaded by the new `form_34`:** H6047's `33 34` bar swap (an
  empty write) and H6057's `aa 34` device detail (`form_34: device_detail`). `govee_profile` gains `f_34`, and
  `govee_state` the `cmd_34_state` / `device_detail` types (`[switch flags][battery][music flags][music code]
  [volume][brightness]`, with `light_open`, `white_noise_playing` and `main_switch_on`). Only the generated enum
  name changes for existing readers (`Command.swap_light` -> `Command.cmd_34`; nothing in the library used it).
  The h6092 rows' `status_read` follows the rename. devices.schema.json: `power_read` gains `aa34_detail`, the
  wire forms `f_34`; the h6057 wire gains `f_34: device_detail`.
- **The H6057 light switch is its own `form_30` member, `light_switch`** (govee_common; `33 30 <1 / 0>`,
  `BleProtocol.command_light_switch`, `H6057DetailVM.h5`), with the write type `light_switch_write`
  (govee_ble_frame `cmd_30_write`, `state` a `light_power`), where `light_direction` had listed it with the
  H6038 direction and the H71xx chart report. `device_detail.light_open` cites `Controllers.d` and names that
  switch as its write; `cmd_30_state` notes that `light_switch` has no 0x30 read. The h6057 wire's `f_30` is
  `light_switch` (was `light_direction`), and devices.schema.json's `f_30` enum gains it.
  `spec/govee_commands.py`'s `build_h6057_light_switch` is the reference the library's `build.light_switch` is
  checked against.
- **`power_write`, a clientRow fact** (devices.schema.json; `tools/gen_devices.py` KEEP): the whole-device
  power write where it isn't the `33 01` main switch. `light_switch` is the only value: the resolver needs
  `power_read: aa34_detail` with it, and the other way round (the light flag is that switch's state), wire
  `f_30: light_switch`, and no zones. h6057 104 states it and gets `support: app_traced`; its doc records both
  header switches, the user's choice and what `turn_off` leaves playing. `power_read: aa34_detail` now reads
  `device_detail.light_open`, not `main_switch_on` (the schema description says so).
- **The device-list card's session, as spec vocabulary.**
  - `clientRow.session`: `held` (absent; the control screen's) or `per_command` (the card's:
    `CardCommManagerProxy.comm2Device` -> `u` -> `triggerBleCmd$2`).
  - `power_read: advert`: no GATT read, the advert's switch byte as the card reads it, and
    `heartbeat: none`: no keep-alive on a path that holds no link (the card's null heart message). A
    `heartbeat_timing` beside `none` is refused by the schema.
  - `timing.per_command`: the 1100 ms spacing, the 29.5 s command bound, the client, the send and the close,
    with the library's deviations (it reads the reply for a refusal) and the citations.
  - The advert's on/off is a `govee_state` `power_state` source, `advert`: the manufacturer data's
    `device_state` read as the power byte, on at 1 for a light (`AbsBaseBleModel.z`) and by the
    `govee_profile` `power_rule.advert_on_mask` for a plug (h5080 `Model.z` / `Model4Plug2.z`).
    `identify.parse_broadcast_onoff` reads both through it (the same decodes as before, now from the spec).
  - The resolver serves `per_command` only on a row that reads nothing, writes nothing on connect, holds no
    link, has no lock, zones or pieces and carries only power and brightness; `advert` and `none` on a held
    row are not served. The coupling stays in the resolver, not the schema, so a firmware-confirmed read could
    later join a `per_command` row (design review R3).
- **h70b2 185 and h6630 285 / 286 get `support: app_traced`** on the card's path (`session: per_command`,
  `power_read: advert`, `heartbeat: none`, `brightness_range: [1, 100]`), with the card trace in `doc` and
  `doc-ref` (card registration, model and BLE object, the frame builders, the slider gate, the session, the
  advert power, the pact gate) and the two facts that rest on the unsaved jadx re-decompile named in prose.
  285 / 286's `heartbeat: heartbeat` was the control screen's held link, not the card's. The reference's
  self-test checks their routes and session.
- **`ic_pieces` gains the app's other piece rules** (devices.schema.json): `rounding: ceil | floor` on the
  clamp-and-divide form, `{table: [{ic, pieces}], otherwise}` and `{pair_max, otherwise}`. h705a 125 / 240
  take `rounding: floor`; h6609 173 takes `{table: [{ic: 36, pieces: 18}], otherwise: 14}` and h61d3 375
  `{pair_max: 15, otherwise: 30}`, each with the `ic_num` read its screen sends (the schema ties the two, and
  neither form could be stated before). No `.ksy` change: govee_state's `ic_count` already has the second
  pair (`has_second_pair`) and the reader's failure (`app_pair_fails`).
- **`ic_pieces`'s object forms size the colour writes as well as the walk** (devices.schema.json): every pick's
  position set, the whole-device mask included, with no cap but the rule's; a row's wire `num_segments` is then
  only the count before the first read, and `sections` sizes the read-back alone. The rows' docs name the
  strip that takes the count. h705a 125 / 240 / 250 and h61d3 299 / 261 become `app_traced` (their docs drop
  the static-mask divergence; h705a 184 too, its first walk the user's approved divergence, and its
  `ic_pieces` gains `skip_zero: true`, an IC of 0 no count), h61d3 375 `app_traced` and h6609 173 `modelled`
  (segments and `color_functions` left out, its doc says why), and h61d3 247's `num_segments` is 14, the most
  `ceil(clamp(IC, 41, 70) / 5)` gives, where it was the library's bound of 16. The h705a 125 / 240 / 250 / 184
  wire rows take `f_40: ic_pair`, the reader their screens read aa 40 with (the pair form needs it), and
  govee_common's `form_40::ic_pair` doc names that read.
- **The 0xAC special-info read.** `govee_ble_frame.ksy`: `status_command` gains `special_info` (0xA1: `ac a1`,
  no args), and the reply chunk's sub byte takes the new `status_sub` enum (`none`, the status read's 0;
  `segment_ic`, 0x0B). `govee_status_stream.ksy` is the joined 0xAC reply of either reader: the root takes the
  `reader` (a `status_command`, the request's command, which the first chunk echoes) after the profile, and
  reads the status read's TLV stream or the special-info read's `segment_ic`, the new `segment_ic_info` record
  (`num_segments`, `ic_per_segment`, Java bytes; `app_read_fails` for an empty payload). No `aa a1` reply is
  typed: no app path reads one, and H707A's status TLV 0xA1, which its screen reads with its own reader
  (`H707AInfo4Detail.G0`: unsigned, IC / 3), stays untyped. devices.schema.json: `connect_reads` items are a
  `connectRead`, a command id or `special_info`.
  The reference's self-test joins a two-packet reply and reads it. Corrections: the read is `ac a1` on proType
  0xAC, not `aa a1 0b` (0x0B is only the reply's sub), and it has callers (`govee_commands.read_special_info_a1`
  and OPCODE_COVERAGE's 0xa1 row said none): goodsType 180's connect read and its refreshes, and H707A-C's
  refresh after its segment page. h705a 180 lists the read, `aa 40` as `ic_pieces: fixed` and a 12-segment
  bound (the segment-settings page's most). devices.schema.json's `ic_pieces: fixed` (and
  `DeviceProfile.ic_num_read_only`) now says what it means on that row too: the aa 40 count does not size the
  pieces, which are the row's static count or, with the `special_info` read, the device-reported one.
- **`color_page`, a clientRow fact** (devices.schema.json; `tools/gen_devices.py` KEEP; the resolver): the colour
  page the screen's `DelegateCurMode.createView:185-203` builds, `stock` (`DelegateColorV2`, whose `W1` shows a
  one-colour Kelvin tint in range as its colour temperature) or `none` (the screen's mode list has no colour
  page), as `DeviceProfile.color_page`. The resolver requires it, and allows it only, on a row with `color_temp`
  whose mode decode (`mode_color_parse` or `mode_color_parse_status`) reports a colour (`COLOR_REPORT_DECODES`:
  `rgb_kelvin_tint`, `rgb_kelvin_tint_positive`, `rgb_kelvin_0d`, `rgb_kelvin_0d_nocct`, `rgb`, `bri_rgb_pair`),
  so the read-side Kelvin-tint fold is a traced fact of each row, not of its decode (Changed). 63 rows say it: 60
  `stock`, each citing its screen's detail config (the `DeviceDetailConfig.l` registration, which sets no
  `Build.U`) and `DelegateCurMode.createView` in its `doc-ref`, and 3 `none` (bulblightv3 328, h6071 57 / 67). The
  schema's description holds the trace, the one `Build.U` override (rgbiclight h6160 `DetailConfig$init$4`) among
  it. The `mode_color_decode` header says a `cct_rule` is the decode's own test and the row's `color_page` how a
  reported colour is shown; `rgb_kelvin_0d_nocct`'s `cct_rule` no longer states the page's rule as the decode's.
- **`clientRow.kelvin_readback`** (`pieces`; absent = the mode report's Kelvin): the screen reads its
  colour-temperature state from the read-back's pieces (goodsType 180's `VM4Light.g5` -> `ColorUtils.r`); the
  resolver takes it with `color_temp` and the direct colour read-back only, on a polled row whose walk is not
  IC-sized. **h705a 180 gets `support:
  modelled`** with `capabilities` `[power, brightness, rgb, color_temp, segments, gradual]` (Config.I[180] all
  on; Config.z / G = 0), `color_functions` and `kelvin_readback: pieces`. Its `doc` names the one gap that keeps
  it from `app_traced`: the colour page refuses a segment-brightness pick while a selected piece shows a tint
  colour (`ColorModeV2` / `ColorMode.canClickBrightness`: `Y` -> `ColorUtils.o`, a toast and no frame), and the
  library sends the `33 05 15 02` frame there.
- **`form_a5` gains `single_record` (3)**, the H703A / H703B status screen's TLV 0xA5: `govee_state`
  `cmd_a5_state` reads it, status TLV only, as the new `color_single_record` (`unread` byte 0 and a `bri_rgb`
  `record`, each read only from a value of 5 bytes or more, `is_read`; `app_applies` at a piece count of 1, as
  `SubMode4Color.l` takes it). It is never an `app_read_fails` term: the screen never throws on it.
  devices.schema.json's wire `f_a5` gains the member, and the h61d3 263 wire gains `f_a5: single_record`; the
  resolver serves it only where the row's `status_read` requests `color_group` over one piece, never for a
  direct read. The reference's self-test reads both lengths and checks that two pieces apply none.
- **`heartbeat_power` and `heartbeat_reread`, a status row's aa 01 keep-alive power and its power-change re-read**:
  devices.schema.json's clientRow takes the boolean and the `{ pieces }` object (and gen_devices keeps both). The
  resolver serves `heartbeat_power` only on an unzoned status row whose `power_live` has `heartbeat_aa01`, and
  refuses such a row without it (every one says it), and `heartbeat_reread` only with it and with the wire group's `num_per_group` (its aa a5 group size, which becomes
  `DeviceProfile.color_readback_per_batch` there). The rows the user's decision 17 named say `heartbeat_power`
  (straightfloorlamp 69, h6069 284, rgbiclight 351 / 352, h7086 293, h7020 296, h7076 313, h3700 383), and so do
  h7020 316 / 318 / 337 / 391 / 411, which run 296's screen (`NewDetailVM.I3:735-742` true, `J3:744-756`; the
  user's decision 26), and h61d3 263, each `J3` re-traced (`I3` true, then `ShareVM.N3` -> `J3`); the schema's
  description and `timing.heartbeat.deviations` name them. The four whose `J3` re-reads
  say `heartbeat_reread`, with the pieces the screen's walk takes: 7 on 69 (straightfloorlamp `VM4Light.p5` -> `t5`,
  `Support.getBulbStringMaxNum(69)` / `getOneGroupColorSize(69)` = 4), 140 on 284 (`CubeVM4Light.h5` -> `l5`:
  the stored shape layout's size, `CubeNewInfo4Detail.v`, from `CubeDataSource.d` through `CubeAcLightDetail.H3`,
  and `Support.getBulbStringMaxNum(284)` = 140 only when none is stored; the library has no layout, so the row
  takes the no-layout count, kept until the stored-IC release reads the layout, the user's decision 26), 15 on
  351 / 352 (rgbiclight `VM4Light.G5` -> `O5` -> `N5`: `Support.b` / `d`
  / `D`) and 1 on 263 (h61d3 `VM4Light.D5` -> `G5` -> `w5`, `H61D3Support.e` / `t`; its wire gains `group: { rule:
  fixed, num_per_group: 4 }`). `timing.heartbeat.deviations` says what the library sends and where it differs (the
  re-read at once on the reply's link, the beat held, the setting reads left out, a miss ending it, the colours
  applied only where they are the row's segments). govee_ble_frame `mode_query` and `group_query` cite the re-read
  (`Compose4Off2On`, the four walks) among their senders. Each row's `doc` says how the library follows its
  keep-alive, in place of the "re-reads the status every poll instead" note. `govee_state`
  `power_state.is_on_heartbeat` cites the newdetail light's rule, on at 1 (`BleComm.G`, `ShareVM.N3`), which
  `parse.parse_light_power(heartbeat=True)` reads. The h61d3 `status_read` note's "absent TLVs leave brightness 0"
  is corrected to 100, the fresh `DeviceBaseInfoFromDevice`'s (`DeviceBaseInfoFromDevice.java:70-80`).
- **TLV 0x40's two one-count readers, the whole-group placement and a status row's IC sources.** govee_common
  `form_40::one_count_u16` reads bytes 0-1 unsigned (`SegmentSettingController.Companion.b`; govee_state
  `ic_count.ic_count_u16`), beside `one_count`, signed (`BleUtil.getSignedShort`), each with no byte-order
  check; `group_count_rule::whole_groups` is fixed records whose status TLV groups are placed only where whole
  within the pieces, later ones dropped without failing the read (`VM4LightH351011$...$1.p`; govee_state
  `color_group.drops_past_whole`). devices.schema.json clientRow: `ic_pieces` on a status row takes its IC from
  `status_read` `ic_num` (TLV 0x40) or from aa 40 reads around it, `connect_reads` before and the new
  `post_status_reads` after; the new `readback_pieces: ic_count` places the status read's colour groups over
  the IC count itself. Rows: h705a 394 and h7057 349 become `app_traced` (349's `by_model` counts, the
  catalogue's IC, are gone: `num_segments` 8 is only the count before the first read); h7020 316 / 318 / 337 /
  391 are written out, `app_traced`; h7020 296 / 318 / 337 / 361 and h7045 take `f_40: one_count_u16`;
  rgbiclight 332 takes `ic_pieces {0, 14, 1}` (its `num_segments` 14 is only the count before the first read).
  The rows' docs state where an IC count of 0 leaves the library apart from the app. The reference self-test
  checks both readers and the whole-group rule.
- **goodsType 411's 0xA3 colour value and its unit's ack.** govee_ble_frame `comm_byte::sub_mode_color_multi`
  (0x6e, BleProtocolConstants) and `upload_ack.multi_result` / `is_multi_ok` (frame byte 3, which a
  MultiController unit reads as its result, accepting it only on a whole 20-byte frame:
  `AbsControllerNoEvent4Multi.onResult`); govee_upload `upload_kind::color_multi` and `color_multi_value`
  (`[op][colour][kelvin][tint][mask]`, Config.gu's Bytes.j / Bytes.t, its mask a `seg_mask` with no cap,
  `len_cap`, and its `len_head` / `len_value` the lengths it is written at); govee_common `seg_mask`'s cap is a
  `u2`, so an uncapped mask can carry more than 255 bytes. devices.schema.json `color_scheme` gains `h3010`;
  h7020 411 is written out with `color_scheme: h3010`, `ic_pieces {15, 4095, 1}` and `readback_pieces:
  ic_count`, `app_traced`. The reference self-test reads the value, its length and the acks, a short one
  refused.
- **The aa 0f string count.** `govee_state` gains `light_num_state` (the `aa 0f` reply: `count`, byte 0 as a
  Java byte; read replies only, not a TLV the status read types), and `govee_common`'s command 0x0f
  `light_num` its doc and citations (`Controller4BulbNum`, `NewDetailVM.s5` / `u5`, and the setting's 33 0F
  write, `NewSettingActivity.T3` / `ControllerSegment`). devices.schema.json
  clientRow gains `light_num_pieces {per_unit}`: the count times `per_unit` is the screen's stored IC, so it
  sizes the writes and the direct read-back as an `ic_pieces` object form's pieces do, and the row's wire
  `num_segments` is the count before the first read. Two ties: a row with support that reads `light_num` has
  it, and it needs that read. The h7020 rows 53 / 54 / 62 / 63 / 100 / 102 / 149 / 161 get `support:
  app_traced` and their `per_unit` (15, 15, 15, 15, 25, 25, 1, 10), and their wire rows `f_15_01:
  kelvin_tint` and `num_segments`, `Support.getBulbStringSetting` [0] x [1] (30, 30, 30, 30, 50, 50, 24, 30):
  the IC `NewDetailVM.u5` stores at the most strings the setting offers (`SegmentNumSettingDialog.t`; on 161
  its dialog shows half of [1] per string, `NewSettingActivity.g4`). The h61a8 bulb-string rows that also
  read aa 0f (bulblightstringv1 3 / 5, h7022 0) stay without support and without `light_num_pieces`: their
  other gaps (the wide 0x0b mask, colour temperature as a tint, the aa a2 read-back) still hold them, and
  goodsType 3's 15-or-30 rule is more than a `per_unit`.
- **TLV 0xA1, the H707A segment info** (govee_common `form_a1`: `unknown`, `segment_info`; govee_profile
  `command_forms.f_a1`; govee_state `cmd_a1_state` -> `segment_info`, `[segment_count u1][segment_ics u1...]`,
  the status TLV only, with `H707AInfo4Detail.G0`, the reply's lambda, `SegmentIcDataCache` and
  `H707AColorMode` cited). An empty value fails the app's read (`app_read_fails`: `G0` reads byte 0
  unguarded). devices.schema.json `wireForms.f_a1`; a status row that requests `cmd_a1` needs `f_a1:
  segment_info`, and the resolver projects it as `status_segment_count`. h707a 326 takes `support:
  app_traced`, the family wire `f_a1: segment_info` and `wire.by_model` counts (`Support.f`), and its doc says
  what the library leaves out. `govee_reference.make_profile` takes `f_a1`, and the self-test reads a 0xA1
  TLV and its empty-value failure. govee_state's `ic_count.segment_signed` doc names the screen's use of TLV
  0x40's segment byte (the IC-pair reader's second value), which sizes the page where the IC changed and no
  0xA1 follows (`H707ABCDetailVM$afterConnectedSingleReadDeviceInfo$1.l` -> `n6`).
- **More rows carry the user's decisions 11, 12 and 13, each re-traced and cited in its `doc` and `doc-ref`.**
  `segment_color_temp` by `SupportPartColorTemConfig.d` (the Part tab's Kelvin item,
  `DelegateColorV2Part.Z0` -> `LightColorCapabilityCenter.C2`): true on h61d3 375 / 261, h705a 125 / 180 / 394,
  h707a 326, h6609 260 and h7057 349, whose strips' `canClickColorTem` tint the selected pieces, and false on
  dreamcolorlightv1 71 / 123, h61d3 299 and h705a 240 / 250. No `segment_brightness_groups`: each of their strips'
  `canClickBrightness` (ColorModeH1A45, ColorModeV1_2, the h61d3 and h705a ColorMode / ColorModeV2,
  H70C456789ColorMode, the h706789 ColorMode, H3401ColorMode, ColorModeH351011, H707AColorMode) sets the selected
  pieces as they are, where `ColorMode14Rect4ReverseSelect` / `H605CColorMode` widen to groups. `device_info_reads:
  none` on the new status rows, h7020 316 / 318 / 337 / 391 / 411 and h707a 326: their composes queue no aa 06 / aa
  07 read (316 / 391 / 411 pass the one `Controller4IcNumber` aa 40 read) and their success callbacks take the
  versions from TLV 0x07 (`NewDetailVM$afterConnectedSingleReadDeviceInfo$1.o` and `...4702ABC$1.o`,
  `H707ABCDetailVM$afterConnectedSingleReadDeviceInfo$1.n`, each through `k2(e(), d())`); h705a 394 and h7057 349
  already had it. The values came in with an earlier merge (the resolver requires them); this adds the traces,
  and no profile, frame or read changes.

## [1.6.1] — 2026-10-21

### Fixed
- **A device whose advertised name isn't a Govee name, or that has none, now gets the cipher its advertisement
  announces.** `create_device`, `make_device` and the `Device` constructor read the encrypt flag and the pact from
  the Govee manufacturer data whatever the local name, as the app's light control screen reads a bound device's.
  `Device.ingest_advertisement` learns the pact (and, as before, the on/off) the same way. Until now the flag and
  the pact were read only under a Govee name (`identify()`, the port of the add-device scan's
  `BaseBleProcessor.parse`); otherwise the profile's default cipher was used and the pact stayed unknown.
  - **What it fixes:** on 2026-10-21 Home Assistant's record for a lab H6008 carried a neighbour's name
    (`5134P0098101970F`) and its company-0x2E28 entry beside the bulb's own Govee block (header 0x43: encrypted,
    pact 1,1). Every session then came up plaintext, which the bulb's SW1.01.25 firmware never answers from a
    normal host, and the light could not be controlled. The bulb itself advertises only `GVH6008…`; the record was
    merged on the ESPHome-proxy path (habluetooth keeps the longer name and unions the manufacturer data) and
    persisted across a Home Assistant restart. The same merge had hit both lab H6008s and an H60A6 since
    2026-10-13. The name gate dates from 1.0.0; reproduced on 1.5.0 and 1.6.0.
  - **What the app does:** the light control screen finds a bound device by its stored MAC (`AbsViewMode4Op.s0`,
    `AbsViewMode4OpWithOtaCheck.s2`; the name only when no MAC is stored) and parses the broadcast's pact block with
    the stored goodsType (`AbsViewMode4OpWithOtaCheck.j1` -> `GoodsType.parseBleBroadcastPactInfo` ->
    `BleUtil.parseBleBroadcastPact`, which skips another company's 0xFF entry). `AbsViewMode4OpWithOtaCheck.w2`
    stores the encrypt flag per MAC (`BleBroadVersionCache.c`/`d`), and `EncryptionUtils.isEncryptionSupported`
    reads it, with the BGC read, at connect. The H5083 plug screen reads the flag the same way. The H5085 / H5089
    plug screen (`BlePactV1.r`) also needs the stored name to match; with a foreign name it connects by MAC on the
    flag it remembered from earlier broadcasts, which is the device's own block again.
  - **What stays:** discovery keeps the name gate. `identify()`, `from_advertisement()`, `scanner.supported()` and
    `scanner.match()`/`discover()` still treat a non-Govee name as not a Govee device, as the app's add-device scan
    does. Without a Govee pact block the profile default is still used (the app's light screen refuses such a
    broadcast, `AbsViewMode4Op.onScanEvent` -> `p0(999)`, or connects by MAC on its remembered flag). A
    Govee-named advertisement reads as before, and the cipher is still fixed when the Device is built: the app
    re-learns the flag from every broadcast, the library does not (yet).
  - **New for such a device, as for a Govee-named one:** its pact now routes the profile
    (`profile_for(sku, pact=...)`), so a SKU outside the pact fallback whose rows refuse the advertised pact raises
    `GoveeBleNotSupported` from `create_device` (`make_device` returns None), as the app's light screen refuses it
    (`AbsViewMode4Op.onScanEvent`: `O1` false -> `p0(999)`). Its flag can also say plaintext. With several Govee
    entries in one merged record, the first one read decides, as under a Govee name. A caller that works out the
    routing pact itself should read it the same way, with `identify.parse_manufacturer_data()`, not `identify()`.
  - No public API change. Traced against the app (decomp 7.7.02) and the H6008 SW1.01.25 image (`FUN_9b024170`
    builds only `GVH6008` + 4 MAC digits; `FUN_9b04341c` -> `FUN_9b0433ec` set the 0x40 bit and pact 1,1).
  - **Live-verified on the bench** (2026-10-21, the Mac, both lab H6008s, reads only): built from the bulb's own
    advert merged with the neighbour's name and 0x2E28 entry, the fix gives `aes_rc4_psk` and pact (1,1), and the
    handshake and all six reads are answered (8 of 8 frames); 1.6.0 gives plaintext and pact None, and none of
    its six reads is answered. From the unmerged advert, the fix gives the same encrypted session on both.

## [1.6.0] — 2026-10-21

### Added
- **`Device.release() -> bool`**: drop the BLE link now and end the 30 s warm window a write opened
  (`const.ACTIVE_IDLE_DELAY`), keeping the Device usable; the next operation reconnects (link, handshake,
  unlock, session frames) with no window left over: a poll gets the base idle delay, a command opens its own
  30 s window. For a caller that knows no follow-up is coming (a voice command, an automation, one device of
  a multi-device burst), so a held link stops holding a proxy connection slot. Asked by the Home Assistant
  integration, to hold the link only while a person uses the GUI.
  - It takes the Device's locks in their documented order (`_scene_lock`, `_bar_lock`, `_read_gate`, then
    the connection's), so each link operation running or queued before it finishes on the current link (each
    lock is FIFO), and it never lands between a scene's upload and its activation or between an H6047 bar's
    `aa 36` read and its `33 36` write. It can land between the steps of a call that no Device lock spans (a
    poll's reads, a plug's power write and its re-read); those later steps reconnect, as does an operation
    queued after it (a plug's re-read ends with its `33 b5` time sync, a write, so that new link opens a fresh
    30 s window). A `set_scene_by_name()` holds `_scene_lock` for the whole call, its version read and catalog
    load included, so a release called after it starts drops the link after the activation.
  - A write called after it either queues behind it and reconnects or, if it reaches the link first (a setter
    that takes no Device lock, or is handed one, while `release()` waits for one), keeps the link and its
    window: the release then drops nothing and returns False. That is checked per link, under the connection
    lock, after every operation queued before the drop: a write its caller cancels (before or after its frames
    go out), or one whose link has gone since, keeps nothing. A write the device refuses (a
    `GoveeBleCommandRejected`) reached the device on that link, so it keeps it too.
  - Returns True when a link was up and was dropped, False when it dropped nothing (no link up, after
    `stop()`, which it never reopens, or a write called after it wrote on the link that is up). It never raises
    a `GoveeBleError`: the teardown is bounded by `const.DISCONNECT_TIMEOUT` and its errors are logged at DEBUG.
    Cancelled while it waits for a lock, it drops nothing; cancelled during the teardown, the teardown runs on
    to that bound. A dropped link logs its own DEBUG line, `link released by caller`.
  - `release()` calls `GoveeConnection.disconnect(keep=...)` and returns its result (what a test double that
    replaces `Device._conn` needs for it is under Changed).
  - The heartbeat stops with the link and the next session arms its own (one chain). A secret set on the
    live session (`set_secret()`) goes out once, in the next session's setup.
  - The warm window it ends lives with the link (see Fixed).
  - Live-verified on the bench 2026-10-20 on an H6006, two H6008s, an H60A6, an H6047 and an H5083 (govee-apks
    library-audit/heartbeat-1.6.0/bench/runs/mac_20261020_232012, run E): `release()` returned True and dropped
    the link at once, nothing went out for the 6 s the link was down, and the next `update()` reconnected on a
    new link with a new session and one heartbeat chain, a beat every 2.00 s.
- `DeviceProfile.heartbeat` (keyword-only, `int | None`, default None) and `DeviceProfile.heartbeat_timing`
  (keyword-only, `"fixed_rate"` or `"after_quiet"`, default `"fixed_rate"`): the command code of the keep-alive
  read the SKU's control screen sends, and when. Every generated profile has one (its devices.yaml row's); a
  profile built by hand keeps 1.5.0's behaviour and sends none. To turn it off for any supported SKU, build the
  `Device` from `dataclasses.replace(profile, heartbeat=None)`, the one field: without a heartbeat the timing
  is ignored, so H5080 / H5083 / H5085's `"after_quiet"` needs no reset and no beat goes out on either timing.
  Validated at construction: one of the four keep-alive command codes (`0x00`, `0x01`, `0x33`, `0x36`) or
  None, and a known timing (checked with or without a heartbeat). Also `build.heartbeat(command)` (the
  `aa <command>` read for one of those four codes; any other `command` raises `GoveeBleInvalidValue`) and
  `const.HEARTBEAT_INTERVAL` (2.0 s) / `const.HEARTBEAT_WRITE_TIMEOUT` (1.0 s).

### Changed
- `Device.set_scene_by_name()` now holds the scene lock (`_scene_lock`) for its whole call: its version read
  (when the scene set waits for one) and its catalog load (a worker-thread hop) as well as its upload and
  activation, where 1.5.0 took it only for those two. So a concurrent scene write (another `set_scene_by_name()`,
  a `set_scene(code)`) and `release()` wait for a catalog load that is running. Polls and the other setters are
  not held back by it.
- A test double that replaces `Device._conn` needs more of `GoveeConnection` than 1.5.0's `Device` used
  (`connect()`, `send()`, `send_many()`, `send_unacked()`, `query()`, `close()`, `update_ble_device()`,
  `encryption`, and `is_connected`, read only while a `set_secret()` has left a `33 b2` pending):
  `hold_heartbeat()`, a synchronous context manager (a no-op is enough), for the calls listed under
  Fixed; `link_number`, an int property read after every write and by `release()` (0, no link, records
  nothing); and, once its tests call `release()`, `async def disconnect(self, *, keep=None) -> bool` (`keep`,
  when given, is asked under the connection lock: True keeps the link and returns False).

### Fixed
- **A held link no longer drops about 10 s after the last command** (#30). The app's control screen sends a
  keep-alive read every 2000 ms while the link is up, and the firmware drops a link that has had no write for
  10-11 s (H6006 `FUN_9b02801c`, and H5083, H60A6 and the H6047 BLE MCU the same; the H6008 has no such timer):
  a field A19 bulb (an H6006 by the device manifest; the address match is inferred) dropped 10.45 s after its
  last write, and the H5083 firmware simulation ended the 30 s warm window (`ACTIVE_IDLE_DELAY`) at about 11 s.
  `GoveeConnection` now sends the profile's heartbeat while the link is up, so a link lasts as long as the
  library's idle policy says and no longer: 8 s after a poll, the 30 s warm window after a write, or for good
  with `idle_disconnect=0`.
  - The frame is the row's (`aa 01` on most SKUs, `aa 00` on H60A6, H60B1, H6098 / H8098 / H6690, H7056 and
    H1630 / R1630, `aa 36` on H6047 / H6042), the read the screen's view model picks (`ShareVM.K3` ->
    `ConnectHeartbeat.c`, an `IHeart`, or the plug screens' heart controllers).
  - Timing: a fixed 2 s rate from the moment the session is set up (handshake, unlock, session writes), so no
    beat goes out at link-up (`AbsBle.HeartRunnable`); on goodsType 43 (H5080 / H5083 / H5085) 2 s after the
    last operation that sent a frame, each one restarting the wait (the ctlchannel `HeartHandler.e`, which
    restarts it on every send; a `connect()` that finds the link up sends nothing and restarts nothing).
  - A tick is dropped, not queued, while an operation holds or waits for the link, or while a poll's reads, a
    scene's upload and activation, an H6047 bar read-modify-write, a plug's power write and re-read, or an
    H6062's `aa 41` segment-count read and the colour write it sizes run (`GoveeConnection.hold_heartbeat`), so
    no beat lands between frames the app sends back to back (the app reads `aa 41` in its connect read, during
    which it sends none). `Device` now calls `GoveeConnection.hold_heartbeat()` (a synchronous context manager)
    in `update()`, `set_power()` (so `turn_off()` too), `turn_on()`, an H6047 bar's `set_zone_power()`, every
    `set_scene_by_name()` (an activate-only scene too) and, on every profile, the colour setters `set_rgb()`,
    `set_color_temp()`, `set_segment_rgb()`, `set_segment_brightness()`, `set_segment_color_temp()`,
    `set_zone_rgb()` and `set_zone_color_temp()`. On an H6062 (the one profile that reads its segment count; it
    has no zones) the first five, like a `turn_on()` with a colour or Kelvin, hold its lazy `aa 41` read and the
    write it sizes; on every other profile they hold only their write. `set_brightness()`, `set_gradual()`,
    `set_scene(code)` and a non-bar `set_zone_power()` hold nothing. What a test double that replaces
    `Device._conn` now needs is under Changed.
  - A beat is one write, bounded at `const.HEARTBEAT_WRITE_TIMEOUT` (1 s, new), never waits for an ack, and its
    failure is logged at DEBUG only. It never touches the idle timer or the warm window, and a disconnect (the
    idle one, `disconnect()` or `close()`) or an operation that wants the link cancels a beat still writing
    rather than wait for it, so no command waits for a beat (the app writes its beat off its command queue) and
    neither the idle close nor the warm window moves. Its reply is not folded into state: it stays in the
    receive queue, which the next operation drains, as an `ee 30` push does (a deviation from the app, which
    folds `aa 01` / `aa 36` / plug replies; the follow-up is to fold heartbeat replies and `ee 30` pushes
    through one hook).
  - Every frame a link held under 2 s sends is unchanged, as is every profile field but the two new ones.
    Home Assistant's 1 s post-poll idle leaves room for at most one beat after a poll; command links now really
    stay up 30 s, holding a proxy connection slot that long. `const.ACTIVE_IDLE_DELAY` keeps its name and value.
  - Every held link now logs each beat as a TX / RX pair, one pair every 2 s, to `frame_log` and to the
    `govee_ble_local.frames` logger (DEBUG): 28 lines (14 beats) in the 30 s warm window of a command that brings
    the link up in under 2 s (the window starts with the command, the first beat 2 s after the session is set
    up; 26 lines for a 2-4 s connect), and 30 lines (15 beats) when the link was already up, one beat fewer for
    each tick an operation or a hold drops.
  - Live-verified on the bench 2026-10-20 on the same six units (the same run, runs A-D): without a heartbeat
    the H6006, H60A6, H6047 and H5083 dropped the link 10.05-10.73 s after the last write and both H6008s held
    it; with the profile's heartbeat every unit held a 40 s watch at `idle_disconnect=0` with a beat every
    2.00 s, the library closed the link 8 s after a poll at the default idle (beats at +2, +4 and +6 s), and it
    held 30 s at `idle_disconnect=30`.
- **A poll that reconnects inside a write's 30 s warm window no longer keeps the new link until those 30 s
  end**: the window now lives with the link, so every teardown ends it (`GoveeConnection.disconnect()`, which
  now returns whether a link was up; the idle one; a failed operation's), and so does a device-side drop. Such
  a poll gets the base idle delay. A write whose link drops mid-write and that is retried on a fresh link
  keeps its window there. Status: source-modelled and unit-tested; not live-verified (the 2026-10-20 bench
  sends reads only, so it opens no warm window).

### Spec
- `devices.yaml` clientRow `heartbeat` (a govee_common command id: `power`, `heartbeat`, `volume` or `cmd_36`)
  and `heartbeat_timing` (`fixed_rate`, the default, or `after_quiet`), required on every row with support, with
  no family default, so an untraced row can't inherit one. All 66 supported rows are set (104 SKUs with the
  modelled ones), each citing its deciding view-model method in doc-ref: 58 `power`, 6 `heartbeat` (`aa 00`: the
  screens whose `I3` is false), 2 `cmd_36` (`NewDetailVm.A3` -> `ComposeLightHeartController`), and
  `after_quiet` only on h5080 43. The traced unsupported rows are annotated too: h6047 277 / 288 / 298
  `heartbeat`, striplight 80 `volume`. The h7040 / h7080 rows' docs note that their screens send `aa 01` only at
  exactly pacts (1,1) / (2,1) and `aa 00` at any other; a test pins those pacts.
- `timing.heartbeat` (typed in the schema): the 2000 ms interval, both timings, the skip rules, what the app
  does with the reply and with a failure, the FIRMWARE-SOURCED drop without it (H6006, H5083, H60A6, H6047; none
  on H6008), and the library's deviations as PROCEDURAL notes (the link follows the idle policy; ticks dropped
  while the link is busy or a sequence holds it; the first beat one interval after the session is set up, and
  on after_quiet the wait restarted when each operation that sent a frame ends; the reply left in the queue,
  with the follow-up named; one bounded write, not retried, cut short by a disconnect or an operation).
- The generator keeps both keys and checks each heartbeat against govee_common's command enum and the
  resolver's `HEARTBEATS` (the four keep-alive reads, whose codes `DeviceProfile` accepts); the resolver
  requires it and checks it one way against `power_live`: `heartbeat_aa01` / `heartbeat_aa36` /
  `heartbeat_aa33` need `power` / `cmd_36` / `volume`, and a relay row `power`. No `.ksy` grammar change (the
  beat is the existing `read_request`); the govee_common `heartbeat`, `power`, `volume` and `cmd_36` command docs
  name their keep-alive use. `spec/docs/GOVEE_BLE_GATT_PROTOCOL.md` describes the heartbeat per screen.
- H60A6 and H6047: hardware-verified that a whole-fixture on restores every zone (2026-10-20, through Home
  Assistant, on the H60A6 'hall 8407' and the H6047 'office bars'; run file govee-apks
  `library-audit/heartbeat-1.6.0/zone_restore_20261021_010747.json`). Each device's name and SKU come from
  Home Assistant's device registry, and the frames named here are the ones the library builds for the run's
  Home Assistant light calls (the fixture's and each zone's turn on / turn off), not captured. With one zone
  switched off while the fixture was on (an H60A6 `33 30` to the main panel, an H6047 `33 36` to the left
  bar), `33 01 00` then `33 01 01` lit both zones again, as the next poll read back; so did an off and on
  alone, and one after both zones were switched off with the fixture off. That is the firmware model
  already cited (H60A6 `FUN_9b0278dc` mode 0: main = zone 0 = zone 1; H6047 `FUN_01004f32` -> `FUN_01002720`:
  both bars = the byte). The devices.yaml h60ax 303 and h6047 119 row docs and their `global_switch` /
  `compose_light_switch` notes carry the marker, and the two rows' doc-refs cite those firmware sites. Doc
  only: no frame, field or reader changes.

## [1.5.0] — 2026-10-20

### Added
- `DeviceState.zone_power_confirmed` (`dict[int, float]`, keyed like `zone_power`): the `time.monotonic()`
  time the device last told the library each zone's power. An entry vouches that `zone_power` still holds
  the value the device gave then. Asked by the Home Assistant integration (latency fix #4: power a zone
  first, and skip that on a zone confirmed lit). It inferred a confirmation from `zone_power` being
  assigned a new dict, which every writer does: an H60A6 `33 30` to the main panel "confirmed" the ring's
  cached power too, and `apply_power` and a scene activation assign one without reading anything.
  - Stamped only by the device: the 0xAC status read's TLV 0x30 (each zone it carries, with the two
    exceptions below), the `aa 36` bar read (both bars), and an acked write whose frame sets the zone. That
    is a `33 30` for its own zone only, a `33 36` for both bars, one `33 30` per zone (no current profile),
    and a `33 01` that sets every zone.
  - The `33 01` case is FIRMWARE-SOURCED; the app's switch ack sets only the main switch
    (`VM4LightH60A6.K5`, `NewDetailVm.F5`). It covers the H60A6's on and off (`FUN_9b0278dc
    @0x9b02791c-0x9b027930`, both zone flags, as the 2026-10-12 bench showed) and the H6047's off
    (`FUN_01004f32 @0x01004f5c-0x01004f60 -> FUN_01002720`, both bar flags). After an H6047 on, the
    library forgets the bars, as the app does.
  - Never by the model: a scene activation, the zones a power write assumes, and an advertisement stamp
    nothing.
  - TLV 0x30 does not stamp a zone, which takes the reported power and loses the stamp it had, when:
    - it reports the zone lit while the same burst's TLV 0x01 does not say on (an "off" still stamps). The
      device says it is off (on the H60A6, TLV 0x01 is the main switch flag), so the zone's flag does not
      say it is lit.
    - on an H60A6, a `33 01` / `33 30` (or a `33 04` wake) set the zone since the last reply that carried
      it, and the reply does not report the one power those writes can leave it at (the last acked `33 01` /
      `33 30`'s, and that of each later write that raised, or may not have posted: a `33 04` wake).
      FIRMWARE-SOURCED: the dispatcher acks those once it has posted them (`MSG_SET_TURN` 0xB2 /
      `MSG_SET_TURN_MULT` 0xB7, `_mqtt_response_pushDeviceMsg FUN_9b027b88`), its message thread applies them
      later, in order (`_mqtt_response_thread_func FUN_9b028fb0` -> `_mqtt_response_handle FUN_9b028bc8` ->
      `FUN_9b0278dc`), and the 0xAC reply is built from the flags as they are (`ble_msg_one_to_more
      FUN_9b04bdd8`), so a reply answered in between reports the old power. The next reply stamps the zone.
  - A zone loses its stamp when the library forgets it: an advertisement's "on" over zones that all read
    off, or an H6047 `33 01` on or scene activation. It also loses it when its value changes without the
    device saying so: an advertisement's "off" over a zone confirmed on. A zone whose value stays the same
    keeps its stamp, except through a scene activation, acked or not, which drops every zone's stamp: what
    it does to the zone flags is not traced (H60A6 `FUN_9b025124`, H6047 handler `0x01005510`).
  - A zone also loses its stamp, with its value left as it was, when the device may hold another power
    for it than the one held:
    - a power write that raises, since it may have reached the device: the transport writes a frame on
      every attempt and once more on a fresh link, so even a refusal can follow an attempt that landed
      unacked. Every zone the write targets loses its stamp, whatever power it is held at: a `33 30` its
      own zone, a `33 36` both bars, and a whole-device power write or a scene activation (a scene's
      power-on lead, its upload and its activation included) every zone.
    - a write that wakes the device while it is not known to be on, which lights every zone (a zone held
      off loses its stamp). FIRMWARE-SOURCED: an H60A6 `33 04` or `33 05` (the dispatcher `FUN_9b02687c`,
      case 4 -> `MSG_SET_TURN(1)` whatever the level, case 5, lights the main switch and both zones, as the
      2026-10-12 bench saw), and an H6047 `33 04` or `33 05` (while both bars are off each handler,
      `0x01005454` and `0x01005510`, runs the power-on sequence `FUN_01002750`, which sets both bar flags).
      The wake is checked again once the frame went out (a power-off may have applied while it waited for
      the link). `is_on` is not trusted to rule a wake out after a write that may have switched the device
      off raised; after an acked zone-off over some zones (an H60A6 `33 30`) unless the device was known to
      be on and every other zone is held lit with a stamp, since a zone-off that leaves no zone lit also
      switches the main switch off (`FUN_9b0278dc @0x9b027972-0x9b027986`: both zone flags 0 ->
      `FUN_9b02b3fc(0)`, then `FUN_9b033e28`); and after a status reply that came before an H60A6 power
      write applied (above). A read issued after the last of these, or an acked write that sets every zone,
      gives the on/off again: a write that fails while a multi-read poll is past its power read leaves it
      untrusted. On an H60A6 a write also counts as a wake while a `33 01` on or `33 30` zone-on is posted
      and no reply has carried it, since the dispatcher checks the main switch as the message thread has
      left it.
    - an H6047 `aa 01` reply that the bars' held power contradicts. It answers bar 0 | bar 1
      (`FUN_01004f32`), so an "off" rules out a bar held lit, and an "on" rules out bars all held off,
      when the `aa 36` read after it is lost.
    - an acked `33 01` off on an H6042 (bars read by `aa 36`, no profile zones): no firmware in hand says
      what it leaves the bars at.
  - It is the last field (the positional order before it is unchanged). It is left out of equality, so a
    poll that re-confirms the same power leaves the state equal to a `snapshot()` taken before it;
    `snapshot()` copies it. `dataclasses.asdict()` and `repr()` still include it, and a read that
    re-confirms a zone gives it a new time, so a consumer that diffs those to detect a change should drop
    it. `dataclasses.replace(state, zone_power=...)` carries the same `zone_power_confirmed` dict into the
    copy, still vouching for the old values: a consumer that replaces `zone_power` should drop the stamps
    too.
  - Additive: no frame, read or other field changes, besides the H6047 bar write's `aa 36` re-read (under
    Fixed), which reads the stamps. The field's first cut (d47d22a) was identical to e7687f4 in a
    differential over every table profile (failed, refused, cancelled and part-failed writes, wakes and
    concurrent setters included); this entry differs from that cut only in that re-read and the field
    itself.
- `const.ZONE_POWER_FRESH_SECONDS` (120 s): how long a `DeviceState.zone_power_confirmed` stamp counts as
  fresh for the H6047 bar write's `aa 36` re-read (see Fixed).
- `DeviceProfile.mode_color_parse_status` (keyword-only, default None): the 0xAC status read's TLV 0x05
  decode where it differs from the `aa 05` reply's (devices.yaml `mode_color_parse_status`). `wire_profile`
  passes it to the spec's `mode_status` param, which the status read now takes (`_spec.profile(mode_status=)`).
  No current SKU sets it.
- `DeviceProfile.scene_versions_by_firmware` (keyword-only, default `()`): `(min_soft_version,
  min_hard_version, versions)` entries that replace `scene_versions` from a firmware version up (a row's
  `by_firmware`; versions compared field by field). A version not yet read meets every gate on it, as the
  newest firmware would, so one failed version read doesn't hold back the other's gates, and `Device.update`
  reads it again on the next poll (Fixed). The dreamcolorlightv1 rows of goodsTypes 70 to 262 hold their
  part-brightness gates in it; no current profile sets it, since none of those SKUs has a catalog yet.
- `DeviceProfile.scene_versions_by_pact` (keyword-only, default `()`): `((pact_type, pact_code), versions)`
  entries that replace `scene_versions` on an advertised pact (a row's `by_pact`), applied before the
  firmware entries. `Device.scene_versions` is the set in force for the advertised pact and the read
  versions, and `Device.scene_upload_frames` uses it. No current row sets it.
- `tools/gen_devices.py`, and `src/govee_ble_local/_generated/devices.py` as a build product (gitignored,
  shipped in the sdist and wheel, covered by the conftest staleness guard, which also watches
  `devices/_resolve.py`, and the backend's `_GENERATED_FILES`). The generator keeps only what the library
  reads, by an allow-list checked against `devices.schema.json`, and fails the build on a missing fact or
  one the library can't serve, never the import. It refuses, among others: a wire `by_pact` / `by_firmware`
  branch (the library reads the wire with both unknown; H60A6's unread pact (1,1) piece count and
  capabilities are allowed as pinned), a mode decode or a command form other than the ones the library
  reads with or sends, a `has_brightness`, `power_live`, `color_functions` or family `encryption` value it
  doesn't implement, a row without `models`, `pacts`, `account_lock`, `brightness_range` (with
  `brightness`), `power_resync` (plugs) or `color_scheme` where one is used, a colour capability on a plug,
  a capability a family-default scheme would drop, out-of-range values (Kelvin and brightness ranges and
  their branches, masks, zone segments, group size, IC range, version gates), `connect_reads` that don't
  list `brightness` / `gradual` exactly where the library reads them, a pact branch at a pact the row
  doesn't list (no device reaches it), and, checked over the whole document, a SKU that a `modelled` row
  and a `hardware_verified` / `app_traced` row both list, or that an `assigned_goods_type` item in any
  family names, and in a family with a supported row a `by_model` or `wire.by_model` entry for a SKU none
  of its rows lists and the catalogue snapshot routes to none of them. Rows of every support level are
  resolved and checked.
- **Routing by pact and goodsType** (one-device-list phase 2): `profile_for(sku, *, pact=None,
  goods_type=None, allow_modelled=False)` and `is_supported_sku` with the same keywords. The candidates
  are the devices.yaml BLE rows that list the SKU and accept the advertised `(pact_type, pact_code)` (any
  row when the pact is unknown), at `goods_type` where given; exactly one (or several with identical
  facts) is the SKU's profile, several that differ are unsupported until the goodsType picks one, and
  none is unsupported, but for 1.4.0's 18 SKUs, which fall back to their one traced profile, as in 1.x
  (see Changed). `create_device`
  takes the pact from the advertisement it is given, so the profile, and every capability, is settled
  before any entity is built; it also takes `goods_type` and `allow_modelled`.
- **The modelled-row opt-in**: `allow_modelled=True` on `profile_for`, `is_supported_sku`,
  `supported_skus` and `create_device` also admits rows whose `support` is `modelled` (inferred, not
  traced). Off by default, so the default table is the user's "app-traced by default"; and as the build
  refuses a SKU that a modelled row and a traced row both list, the opt-in only adds SKUs. The `modelled`
  rows are listed under Spec. `scanner.supported`, `scanner.match` and `discover` take it too, so an
  opted-in caller's scan reports those devices as supported.
- `DeviceProfile.status_read` (keyword-only, default `(0x41, 0x30, 0xA5)`, 1.x's request): the TLV types
  the 0xAC status read requests, in order, from the row's `status_read`. `build.status_query(types=)`
  builds a request from them (`full` stays for the H60A6's default).
- `DeviceProfile.status_decode` (keyword-only, default the H60A6 status screen's, 1.x's decode): how the
  0xAC reply is read, the row's wire facts as `(govee_profile param, value)` pairs: the forms of the TLVs
  the status stream reads, the colour-group form where it reads TLV 0xA5 as colour groups (rule, group
  size, 4-byte records), and the TLV 0x05's music layout and empty-value rule. `wire_profile` carries it
  on a `readback="status"` profile, and `reassemble.status_profile` reads the stream with it.
- `build.color_rgb_frames(..., pieces=)` and `build.color_temp(..., pieces=)`: an `h6061` device's panel
  count, which bounds the layout and sizes the CCT mask (`ColorPieceConfig.k`, the count rounded up to
  whole bytes); None keeps the H6061's 23. `Device` passes its profile's `segments`.
- **The catalogue snapshot routes the SKUs no devices.yaml row lists** (`spec/govee_catalog.json`, read
  only by the generator, `_resolve.catalogue`; no cloud call at run time). Such a SKU routes to the BLE row
  with support at its catalogue goodsType, never at goodsType 0, when that is the only row at it, as the
  app's `Pact.f` routes it. It takes the row's facts (and shares a profile where the resolved facts match),
  at the row's support level capped at `app_traced` (`_resolve.catalogue_support`): the snapshot's
  goodsType is no bench check, so the catalogue bulbs on the `hardware_verified` bulb row are `app_traced`,
  and a `modelled` row's stay behind `allow_modelled`. The generated row carries them apart from its own
  SKUs (`catalogue_models`, a key the generator adds), and each target row's doc records the per-SKU sweep
  of the app for them: every string literal naming one, and its mode components.
  - **10 SKUs gain a route**, by the catalogue's goodsType alone, with no bench check. Nine are on
    goodsType 64, the bulb row: H6009, H600A, H600B, H600C, H600D, H6010, H6011, H6013 and H800B. They
    take the bulb row's facts but offer no scenes until a catalog is bundled (Changed), so they form two
    profiles of their own: H600A / H600B / H600C / H600D / H6011 / H6013 / H800B, and H6009 / H6010
    (`scene_power_on`). The H6004 / H6006 / H6008 profile's `skus` is those three (1.4.0 had a profile for
    each of them: Changed, "Profiles group SKUs with identical facts"). The tenth, **R1630**, is on the
    h1630 row 364 and shares H1630's profile. A catalogue SKU takes its row's support level, capped at
    `app_traced`, so R1630 is admitted by default now that the app trace makes that row `app_traced` (Spec).
  - The admit commit's rows (Spec, "41 more SKUs") extend the catalogue routes to 14 SKUs: **H6167**
    (dreamcolorlightv1 83), **H616D / H616E** (207) and **H802A** (74) join those rows' profiles.
  - The snapshot's refresh at AppVersion 7.7.02 (Spec) adds a 15th, **R14B1**, on H14B1's row (bulblightv3
    408, `app_traced`), so it is admitted by default. It has no bundled scene catalog, so it offers no scenes
    and shares row 111's profile (H1501 / H601A / ... / R1501), whose `skus` gains it last. H14B1 keeps its own
    profile. No other SKU's route or profile field changes.
  - They are served only at the row's pacts, as every SKU outside 1.4.0's 18 is (see Changed): the app's
    scan refuses a device whose pact its goodsType doesn't accept (`AbsBaseBleModel.e`, `checkProtocol` ->
    `Pact.i`).
  - The nine bulbs and the six others offer no scenes until a catalog is bundled for them (scenes need
    a catalog: Changed). Each row's doc says how they route.
  - A SKU that a row lists keeps the spec's route. A non-zero catalogue conflict leaves the SKU unrouted
    and on the review list the generator prints, unless an `assigned_goods_type` item of the row's family
    cites the app's rewrite of that goodsType: one whose `from_goods_type` names it, or, with none, one
    that the pact conditions. A SKU no row lists also goes on the list when the app rewrites its goodsType, or when rows of
    several families share that goodsType.
  - Today the list holds H615E alone. The 8 non-zero conflicts are all on rows without support, so none is
    unrouted. Seven are such a rewrite: H6145 / H6146 / H6147 / H6171 13 → 17, H6118 / H6194 6 → 39 and
    H6179 12 → 39. H615E's item is the unconditioned pairing write of 40, which cites no rewrite of its
    catalogue 108, so it is for review.
  - No route or profile field of 1.4.0's 18 SKUs changes. The snapshot's ic is read nowhere: no row's app
    trace uses it. The conftest staleness guard watches the snapshot.
- **Pact-conditioned reassignments route by pact.** The 11 `assigned_goods_type` items whose condition
  tests the pact alone (`min_pact_type` / `min_pact_code`) are now routing data instead of a refusal. Each
  SKU is listed on the row of the goodsType the app rewrites it to, with the pacts at or above the gate. It
  belongs on a row of the goodsType it is rewritten from with pacts below the gate, and no such row exists
  yet. The advertised pact then picks the row the app's rewrite picks.
  - `_resolve.routes` checks every such item against the rows. The build fails when a row lists one of its
    SKUs at the target goodsType with a pact below the gate, at a source goodsType with a pact at or above
    it, or at either with `any`.
  - The 5 firmware-conditioned items and the 2 unconditioned pairing writes stay refused on a routed SKU.
    The generated table now keeps each item's `from_goods_type` and `condition`, so the check reads the
    same there.
  - The generator prints what is left for review (`_resolve.reassignment_review`). That is the source side
    of every item, 20 SKUs, since no row lists them at the goodsType they are rewritten from (0, 13, 6 or
    12). It also lists H6119, which h7090's row 44 lists without pacts.
  - `profile_for`'s 1.x fallback for a pact no row accepts covers none of these SKUs (only 1.4.0's 18), so
    a pact below the gate never reaches the target row.
  - No row involved has support, so no SKU's library route changes yet, and 1.4.0's 18 SKUs keep every
    route and field.
- **A light's connect write** (device-list phase 3): `DeviceProfile.light_effect_stop` (keyword-only,
  default False) and `build.light_effect_stop()`, the `33 25 01` the bulb screens write on connect, once
  their connect reads succeed (bulblight `VM4Light.i5`). A light row may now list
  `connect_writes: [light_effect_stop]` (the resolver allowed connect writes only on plugs,
  `plug_sync_time`); the library sends it once per session after the handshake and any unlock, as it sends
  a plug's time sync. The app sends it once per screen connect, after its connect reads. The library
  connects for every poll after the idle disconnect and makes those reads on its polls, so the write goes
  out on every such poll, before the reads; it only stops a running `33 24` effect. It is sent without
  waiting for its ack (`GoveeConnection(session_ack=False)`; a plug's time sync still waits), so a device
  that doesn't ack it costs no ack timeout per poll. Source-modelled, not live-verified. Set on bulblightv3
  111 / 348 / 408: H1501, H601A, H601B, H601C, H601D, H801A, H801C, H801D, R1501, H1401, R1401 and H14B1
  (Spec).
- **The `aa a3` read without the gradual control** (device-list phase 3): `DeviceProfile.gradual_read_only`
  (keyword-only, default False). Some screens read the gradual flag at connect (the default
  `Compose4InfoBleIot` read) but offer no gradual control; a row may now list `gradual` in `connect_reads`
  without the capability (the resolver tied the read to the capability), and `Device.update` then reads
  `aa a3` on every poll and discards the reply (`state.gradual` stays None). Only a polled light row may
  list it, where the default read is traced; the build refuses it on a status or plug row. The capability
  still needs the read. Set on the ten h7050 rows (Spec): H7050, H7060, H7051, H7062, H7065, H7055, H7066, H7052, H7053
  and H7063.
- **Kelvin and brightness ranges by pact** (device-list phase 3): `DeviceProfile.kelvin_by_pact`
  (`((pact_type, pact_code), min_kelvin, max_kelvin)` entries) and `DeviceProfile.brightness_by_pact`
  (`((pact_type, pact_code), (min, max) | None)`, None = percent), both keyword-only, default `()`, from a
  row's `by_pact` `kelvin_range` / `brightness_range` (the resolver refused both). They replace the base
  range on that advertised pact, as `scene_versions_by_pact` does: `Device.min_kelvin` / `max_kelvin`, the
  brightness write and every brightness read-back (`aa 04`, the status TLV) use the range in force;
  `_fold.brightness_range(profile, pact)` gives it. Until a pact is heard (the app always knows it; the
  library may not), the Kelvin range is the one valid on every pact, the base cut to each entry's, and a
  profile with `brightness_by_pact` neither writes nor reads the brightness byte: the write raises
  GoveeBleNotSupported and `state.brightness` stays None (`_fold.brightness_pending`), since the pact picks
  what the byte means. A pact Kelvin range needs a base one (the row's or the family's) that it overlaps, a
  range valid on every pact and no Kelvin firmware branch, and a pact brightness range the brightness
  capability. `kelvin_by_pact` is set only on h61d3 261 (H61F2 / H61F5 / H61F6, behind
  `allow_modelled`), and no row sets `brightness_by_pact` yet.
- **Kelvin ranges by soft version and per model** (device-list phase 3): `DeviceProfile.kelvin_by_software`
  (keyword-only, default `()`), `(min_soft_version, min_kelvin, max_kelvin)` entries applied with the
  device's soft version (`aa 06`) as `kelvin_by_hardware` is with the hardware version: compared as
  strings (`KelvinConfig.Config.Z2`), and until the version is read the range valid on every revision
  applies. A row's (or a SKU's `by_model`) `by_firmware` Kelvin item may now gate on `min_soft_version`
  alone, not only on `min_hard_version` alone (one kind per row). `client_profile.by_model.<SKU>` may carry
  `kelvin_range` and `brightness_range`, used where the SKU's row has none (the row's wins, as the schema
  says; one under the row's own raises), and a `by_firmware` Kelvin item over its own `kelvin_range`
  (bulblightv3 H14A1). Since a gate compares as a string, the build refuses one that isn't the app's
  `a.bb.cc` form with two-digit fields, and any Kelvin firmware gate on goodsType 235, whose
  `KelvinConfig.Config.Y2` compares the version numerically (`BleUtils.isHigherVersion`).
  `kelvin_by_software` is set on H1401 / R1401 (bulblightv3 348); no row uses a `by_model` range yet.
- **The `aa 40` read that does not size the pieces** (device-list phase 3): `DeviceProfile.ic_num_read_only`
  (keyword-only, default False). Some screens read the IC count at connect although their piece count is
  fixed; such a row now says `ic_pieces: fixed` (the resolver served the read only with the IC sizing), and
  `Device.update` then reads `aa 40` on every poll, before the colour read-back, and discards the reply; the
  read-back walks the static pieces. An `ic_num` read still needs `ic_pieces` (now the sizing or `fixed`)
  and the direct colour read-back, and `ic_pieces` the read; H6641's live sizing is unchanged. No row says
  `ic_pieces: fixed` yet, so no SKU sets it.
- **H6009 / H6010 power on before a scene** (the review's A3d): `DeviceProfile.scene_power_on` (keyword-only,
  default False), from a SKU's devices.yaml `by_model` `scene_power_on`. The app's scene apply prepends the
  power-on switch (`33 01 01`) for exactly these two SKUs (`ApplyOffNeedOpenConfig`, `ApplyOffNeedOpenHelper`)
  while its stored switch state is off, and reads an unknown state as off (`DeviceSwitchConfig.getSwitch`), so
  `Device.set_scene` and `set_scene_by_name` send it first, ahead of any upload, unless `state.is_on` is True.
  H6004 / H6006 / H6008 and every other SKU send what they sent.
- Bundled scene catalogs for the newly admitted models whose profile offers scenes: H6002, H1401 / R1401
  and H14B1 (H14B1 shares H6004's scenes), fetched from the public scene library with `tools/fetch_scenes.py`
  into the scene store. The other newly admitted models get no catalog yet, so they offer no scenes
  (Changed). Every scene in them carries its blob. H1401 / R1401's Christmas is a well-formed 82-byte 0x93
  RGB scene and uploads as one; it is no placeholder (Changed: the placeholder flag). H14B1 shares H6004's
  scenes, whose Christmas, the same scene, loses 1.4.0's `placeholder` flag with the re-fetch (Changed),
  which only `Scene.placeholder` reads.
- **A poll can hold its connect to tighter limits**: `Device.update(*, connect_attempts=None,
  connect_timeout=None)`, so a background poll at a device that doesn't answer gives the device lock up
  sooner and a command queued behind it waits less of its caller's own deadline (the command's own 45 s
  `OPERATION_TIMEOUT` starts only once it holds the lock). `connect_attempts` is bleak-retry-connector's
  `max_attempts` for each connect of that poll (the attempts that time out or fail outright; it still
  tries again after transient failures such as a missing device or no free connection slot, giving up at
  the 9th, so pair it with `connect_timeout` to bound the time); a link that never came up is not tried
  again, and one whose session setup failed is still re-established once (`SESSION_ATTEMPTS`). So
  `connect_attempts=3` is not the default: the default establishes a link that never comes up twice (3
  attempts each), an explicit 3 once. `connect_timeout` bounds each connect of that poll (the link, the BGC
  read, the handshake, the unlock), not its reads: when it runs out, the link the library holds is torn
  down, its disconnect (and any that an earlier operation's cancellation left running) waited for up to
  `DISCONNECT_TIMEOUT` (5 s) more and abandoned at that bound, and only then is `GoveeBleTimeout` raised with
  the device lock free, so the next operation connects afresh with no disconnect of an old link still
  running. When that wait would run past the operation's `OPERATION_TIMEOUT` (a `connect_timeout` above
  `OPERATION_TIMEOUT` − `DISCONNECT_TIMEOUT`, 40 s, or a later connect of the same operation, such as the
  pending unlock's write retry), the deadline ends it instead: `GoveeBleTimeout` ("operation exceeded 45s") is
  raised at the deadline, and the teardown is left to its abandon at its own `DISCONNECT_TIMEOUT` bound, as on
  any deadline; a poll its caller cancels during that wait likewise gives the lock up at once, leaving the
  teardown to that abandon, as at the defaults. The limit is per connect, not per poll: a poll whose link drops
  between its reads connects again with the whole limit, so one that connects k times can take k times
  `connect_timeout` (plus each teardown) in its connects. A connect cancelled inside bleak is cleaned up by
  the BLE backend, which only `OPERATION_TIMEOUT` bounds: BlueZ disconnects it; bleak's CoreBluetooth backend
  (macOS) leaves the pending connection request, so the device may stay connected to the host. They hold
  every connect the call makes (normally its first read's; also a reconnect after a link lost mid-poll) and
  nothing else: setters and other calls keep the defaults, and `update()` without them is unchanged
  (`CONNECT_MAX_ATTEMPTS` 3 per establishment, the 45 s `OPERATION_TIMEOUT`). An attempt count that isn't an
  integer of at least 1, or a timeout that isn't a finite number above 0, raises `GoveeBleInvalidValue` before
  any I/O. On the transport, `GoveeConnection.query` and `send` take the same limits as `connect_budget`
  (`transport.connection.ConnectBudget`).
- `scenes.load_scenes`, `scenes.async_load_scenes` and `scenes.scene_names_by_code` take `scene_versions=`
  (keyword-only, default None): the catalog the Govee app's scene list shows at that scene set (devices.yaml
  `scenes.list_filter`), normally `Device.scene_versions`. None gives the SKU's whole bundled catalog, as
  before. The loader does not know the device table, so a set the store records nothing at (the union of the
  SKU's sets, or one the SKU never has) gives the whole catalog. A `scene_versions` that is not a collection
  of non-negative ints raises `GoveeBleInvalidValue`: a `collections.abc.Collection`, read once, so a
  generator or another one-shot iterator is refused (passed twice, it would read as the empty set the second
  time), a bytes, bytearray or memoryview counts as its ints, and a version too long to write as a key
  (`10**5000`) is refused too; the message names the type, never the value. The store is read and checked
  once, as before, and the catalog of every set is built then: every later lookup, at any set, is from
  memory.

### Changed
- **A device offers the scenes the Govee app lists at its scene set** (review G1). `Device.scene_names`,
  `Device.active_scene` and `Device.set_scene_by_name` use the catalog at `Device.scene_versions`.
  - The app's list drops each lightEffect whose op type is not in the set and plays the first one left
    (`SceneDataVM.b2` -> `CategoryV1.checkSupportNew`; `BaseSceneViewMode.U2`). So at a smaller set a scene is
    hidden, or plays another of its effects.
  - A hidden scene is not in `scene_names`, and `set_scene_by_name` refuses it with `GoveeBleUnknownName` (a
    `GoveeBleNotSupported`), sending nothing: "unknown scene 'Awaken' at scene set [0, 1, 2]". `active_scene`
    is None for its code, and for the code of an effect a listed scene does not play there: the app's list
    highlights the scene whose effect at position 0, the one it plays, has the reported code
    (`SceneListAdapter`, `getSceneCode(0, sku)`; `SceneDataVM.i1` only picks the category page).
  - A scene that plays another effect keeps its name, and uploads and activates that effect. The upload is
    planned as before (`ScenesOp.parseSceneV1` uploads whatever effect it is handed); `set_scene_by_name`
    takes `scene_versions` once per call, for the lookup and the plan alike.
  - **No bundled catalog has a smaller scene set yet**, so no device offers or sends anything else in this
    release: the H61A8 keeps {0, 1, 2, 3} at every pact and firmware, as 1.4.0 (Spec: its scene rule is
    dreamcolorlightv2's, which has no pact gate). The rows that do have one, the dreamcolorlightv1 goodsTypes
    70 to 262 ({0, 1, 2} below part brightness: H6168 at soft 1.05.00, for example, hides scenes such as
    Awaken there and plays Halloween's RGB effect, code 110, in place of 2497), are offered scenes once
    their catalogs are bundled.
  - Names never change; only which scenes are offered. The `GoveeBleUnknownName` message for any name not
    listed now names the set, on every SKU: "H6008: unknown scene 'X' at scene set [0, 1]" (its class is
    unchanged).
  - Before the device-info read the set is the newest firmware's (a version not yet read meets every gate), so
    that read can only shrink the list. `set_scene_by_name` reads a version the set depends on first (`aa 06`
    / `aa 07 03`, on the same connection), so it never plays a scene, or an effect, the device's own set
    does not; `scene_names` may list the newest firmware's scenes until the read. A change of the advertised
    pact can move the list either way, on a row with a pact scene set (none today). Both notify the
    callbacks. Once the store is loaded (`update()` loads it off the loop) `scene_names` and `active_scene`
    read no file and raise nothing, at any set.
  - **A consumer that keeps `scene_names` must read it again on each update**, including the released Home
    Assistant integration v1.4.1, which sets its `effect_list` once, at setup (its `light.py`): a name the
    library no longer offers at the device's set then fails loudly (`GoveeBleUnknownName`) when picked.
    hass-govee-ble-local's release branch reads it again on every coordinator update.
- **`tools/fetch_scenes.py` evaluates the app's list filter at each of the SKU's scene sets** (its row's,
  `by_pact` and `by_firmware` sets, the empty one included) and stores, per scene, each set where the result
  differs from the union's (`by_set`, below). A scene whose pick at a set is an effect the library can't store
  (version rules, no code, a sceneType-5 upload, a placeholder, an entry the shared check refuses, such as a
  code past 0xFFFF) is hidden at that set only, and printed, never a failed fetch; its entry at the union, the
  pre-read list and the tombstone are what they were. A value read as an int that is not one (JSON's 1e999
  among them) fails the SKU's fetch, whichever lightEffect holds it (the tool's int check, below). A scene
  listed only at an empty set (one the union refuses) is left out and printed: names never come back after
  the read. The empty-set note is gone.
  - Naming ignores the per-set results, so a change to a scene's other effects never renames it, and a
    retired name keeps its entry at the union only. Alike twins (one sceneName and one entry at the union)
    take the order of their whole lightEffect content, not the response's, so a reordered response never
    swaps which name plays which effect at a set, and a first fetch names them alike whatever the order.
  - Each scene whose result at a set is not the one the store held is printed by name, per set: hidden
    there, plays an effect there (its code and category), changes from one effect to another, or listed there
    again.
  - A kept scene the fetch no longer lists keeps its results at the SKU's current sets only: one at a set the
    SKU no longer has is dropped, printed, and at a set added since, the scene is hidden where its entry's op
    type is outside the set (the app plays a later effect there, or hides it). A kept scene's effect at a set
    that the sceneType-5 cut newly drops is not listed at that set, printed, rather than the scene dropped.
  - `--audit` plans each SKU at each of its scene sets, one row per set: every scene of the catalog offered
    there, the effects it plays there included, through the upload planner at that set.
  - No bundled catalog has a smaller set yet (H61A8's is its only one), so the store's `by_set` is `{}`;
    the dreamcolorlightv1 catalogs follow in 1.5.1.
- **Scenes need a bundled catalog**, as every SKU of 1.4.0 that offers them ships one. The generator bakes
  the scene store's SKUs (`scenes/store.json`'s `skus`) into the table (`_generated` `SCENE_CATALOGS`), and a profile
  offers `SCENES` only to them; the row's scene facts are still checked. So no SKU offers an effect list
  with nothing in it (Home Assistant showed only "off" for the nine catalogue bulbs). The 75 admitted SKUs
  (and 5 behind `allow_modelled`) whose row offers scenes but have no catalog offer none until theirs is
  bundled. `tools/fetch_scenes.py` with no SKUs lists the SKUs whose row offers scenes, catalog or not.
  A test ties the store to the table: the SKUs with a catalog are exactly those offered scenes, the store is
  the fetch tool's layout, and every catalog's scene count is pinned, with each catalog pinned to its
  released content (`RELEASED`: every `Scene` field of every scene, in order), so a change at the same count
  fails.
  `scripts/smoke_artifacts.sh` loads every bundled catalog from the installed wheel. 1.4.0's 18 SKUs keep
  their scenes.
- **The 0xAC status request is built from the profile**, not hard-coded to the H60A6's: the resolver takes
  the row's `status_read` (command names, checked against govee_common's `command` enum by the
  generator) instead of refusing anything but `[cmd_41, cmd_30, color_group]`, and a pact branch may drop
  `color_group` (the `status_color_groups_pact` gate). The H60A6 sends the same bytes: `ac 03 03 41 30 a5`,
  and `ac 03 02 41 30` at pact (1, 1).
- **The 0xAC status reply is read with the row's own wire facts** (`DeviceProfile.status_decode`), not the
  H60A6 status screen's: the TLV forms, the colour-group rule and group size (the segment stride too), and
  the TLV 0x05's music layout and empty-value rule. The build no longer requires a status row to have the
  H60A6's forms, groups of 4 and status mode; it requires `f_30: zone_power` where `cmd_30` is requested
  (the library takes the zone bits from it) and, where a requested `color_group` is read as colour groups
  (`f_a5: color_group`), a group rule, a group size and 4-byte records. A 0xA5 TLV the row doesn't read as
  colour groups is skipped instead of failing the read, and so is a 0x30 TLV it doesn't read as zone bits
  (its `f_30` is not `zone_power`). No colour group sets a segment at or past the device's piece count
  (`segments`), whatever its record count. The H60A6's decode is its row's, so it
  reads every reply as before, the empty `[05][00]` music value included.
- **A SKU that several rows list is routed, not refused** (see `profile_for`), where the rows are all
  `hardware_verified` / `app_traced` or all `modelled`; the build still refuses a SKU that rows of both
  kinds list. `_resolve.routes` gives the SKUs one row routes, and `_resolve.profiles` each
  group's routes (goodsType, accepted pacts). `create_device`, `scanner.supported` and `scanner.match` now
  pass the advertised pact, which picks between a SKU's rows (`Support.addSupportPact`). Every SKU is
  served only at its rows' pacts, as the app's scan serves it: `AbsBaseBleModel.e` (`checkProtocol` ->
  `Pact.i`) refuses a pact the goodsType doesn't accept, and the accepted pacts compare exactly
  (`Protocol.c`). The one exception is the 1.x compatibility rule (decision D3,
  `_resolve.PACT_FALLBACK_SKUS`): at a pact no row accepts, each of 1.4.0's 18 SKUs still routes to its one
  `hardware_verified` / `app_traced` profile, as in 1.x, so no released unit is refused. A SKU admitted
  since is not: H617C or B7080 at pact (1, 1), or H8098 at a pact its row doesn't list, is unsupported.
  Without a pact (no advertisement, or one without pact info) nothing changes.
- **The direct `aa a5` group size is the spec's**: `color_readback_per_batch` comes from the row's wire
  `group.num_per_group` (4 on the four direct read-back SKUs, as before), and the table no longer refuses a
  direct read-back in groups other than 4.
- **The H6061 panel count is the profile's** (`segments`, the row's wire `counts.num_segments`, 23 on the
  H6061, as before) instead of the builder's 23, so the other Glide goodsTypes' counts (20 to 25) can be
  written once their rows are admitted.
- **The supported-device table is generated from `spec/devices.yaml`** (one-device-list phase 1). The hand
  table in `devices/profile.py` is gone: `PROFILES` is built from the supported `client_profile` rows,
  which `tools/gen_devices.py` projects into `_generated/devices.py` at build time and
  `devices/_resolve.py` resolves. `PROFILES`, `profile_for`, `supported_skus`, `is_supported_sku`,
  `create_device` and `__all__` keep their meaning, and the 18 SKUs keep every profile field but these,
  which follow the spec:
  - H6006 / H6008 `scene_versions` {1} -> {0, 1}, and H6641 {0, 1, 2, 3, 10} -> {0, 1, 2, 3}, the control
    screens' sets. No frame changes: no library scene needs version 0 or 10.
  - H6047 `mode_color_parse` is `kelvin_pair_15`, the spec's name for its bars' 0x15 decode, which is
    unchanged.
  - **H6641 gradual** (`gradual=True`): `Device.update` reads `aa a3` on every poll and `set_gradual`
    writes `33 a3` instead of raising, as its control screen does, and `state.gradual` is a bool, not None.
    Bench-checked on an H6641 (firmware 1.01.13, 2026-10-18, govee-apks
    `library-audit/BENCH_h6641_gradual.md`): it answers `aa a3`, acks `33 a3 01` and `00` and reads each
    back. Its BLE brightness steps fade with the flag on or off.
- **Profiles group SKUs with identical facts.** H6004, H6006 and H6008 share one profile, so
  `profile_for("H6006").skus[0]` and `profile_for("H6008").skus[0]` are now `"H6004"`. Only a `Device`
  built without `sku=` sees it (`create_device` always passes the SKU). Profile order follows
  devices.yaml.
- `DeviceProfile.mode_color_parse` takes any name of the spec's mode_color_parse table (devices.yaml
  `mode_color_parse`), not a closed `Literal`, and `wire_profile` reads the decode from that table
  (`_MODE_DECODES` is gone). `ModeColorParse` is now `str`; `"rgb_kelvin"` stays the default for a custom
  profile.
- The fallback cipher, used only when no advertisement says, is AES-RC4 on account-locked rows (the plugs)
  and on H60A6's row (303), the 1.x compatibility rule, as before, and none elsewhere (decision D4). A newly
  admitted SKU follows its advertisement.
- The table's coherence checks (a capability its colour scheme can't express, a live IC read without its
  clamp) run when the table is built, in
  `devices/_resolve.py`, not when `govee_ble_local` is imported. `DeviceProfile` still validates every
  field of a custom profile.
- Building from the repository also needs PyYAML, which the in-tree backend asks the frontend for
  (`get_requires_for_build_*`). An sdist ships the generated table, and the runtime dependencies are
  unchanged: nothing under `src/` imports yaml. `tools/gen_kaitai.sh` writes the table after the Kaitai
  classes, with `$PYTHON`.
- **Bundled scene catalogs are stored once per scene**, in one file, `scenes/store.json`, in place of 1.4.0's
  `<SKU>.json` files. Its `pool` lists each bundled scene once (its name and entry), sorted by content (the
  name, then the entry's JSON with sorted keys), and its `skus` maps each SKU to the increasing indexes of
  its scenes in the pool, so SKUs that share a scene share its entry. As released, after the catalogs'
  re-fetch (below), the 14 catalogs' 1,010 scenes are 715 entries, the store is 194,010 B, 38,549 B deflated
  in the wheel, and the wheel is 323,432 B. Measured when the layout landed, over the catalogs as they were
  before the re-fetch: 1.4.0's ten catalogs (748 scenes) were 512 entries, and with the admitted models' four
  catalogs (Added), 1,007 scenes were 614; the store was 165,015 B, 35,866 B deflated in the wheel, against
  178,840 B and 46,066 B for 1.4.0's ten catalogs (and 150,289 B and 39,106 B for a layout of one file per
  distinct catalog, with an index, which it replaces before release), and the wheel 295,748 B against that
  layout's 300,604 B. The layout itself changes no scene: over those catalogs, for every bundled SKU,
  `load_scenes` (every `Scene` field), `scene_names_by_code` and `scene_upload_frames` at MTU 20, 182, 244
  and 509, at each of the SKU's scene sets, gave what they gave before it, and for 1.4.0's 18 SKUs what
  1.4.0's wheel returns at the same scene versions. The catalog changes are the re-fetch's (below).
  `load_scenes`, `async_load_scenes` and
  `scene_names_by_code` are unchanged, and a SKU the store has no catalog for still gets `{}`. The store is
  read and checked once, on the first lookup of any SKU (not on each SKU's first lookup), even where
  several first lookups run together (one per device's setup), and one that is
  missing, unreadable or malformed raises GoveeBleError for every SKU: that first failure's message is kept
  (it names the cause's type and text), and with no second read (a package file changes only with a
  reinstall) the lookup that read the store raises a GoveeBleError of it from its real cause, and every
  later lookup a fresh one from None. No exception is kept, so no traceback and no caller's frames, nothing
  a caller does to the error it was given (a `raise err.__cause__` put its frames into the one cause every
  later lookup chained, for the life of the process), and not the exception the first lookup's caller was
  handling. A read error of the process's state (too many open files, out of memory: EMFILE, ENFILE,
  ENOMEM, EAGAIN) and a RecursionError (a first lookup made near the recursion limit, which left the store
  unreadable for good, one a frame from it included, which escaped as itself; and a store nested too deep,
  which every lookup now reads again) are not kept, so the next lookup reads the store again. A device's
  `scene_names` and `active_scene` raise a failed load's error (any, kept or not) with no I/O, and its next
  `update()` loads the store again in a worker thread: `update()` marked the catalog loaded though its load
  failed, so after a failure the loader does not keep every later read re-read the store on the event loop.
  A lookup the first read
  makes on its own thread (a signal handler, a finalizer or a trace hook run inside it) reads the store too:
  the lock across the first read is re-entrant, where a plain lock deadlocked that thread, every later
  lookup and the event loop. An entry `tools/fetch_scenes.py` refuses to write (the loader and the tool share one check:
  Fixed), an index outside the pool or out of order, a pool out of its content order or holding one scene
  twice, two scenes of one name in a catalog, a SKU with no scenes or not in upper case, and an entry no SKU
  holds (an orphan) are refused. `tools/fetch_scenes.py` reads and writes the store: each write lays it out
  again from every SKU's catalog (`layout`) and checks the text it writes as the loader reads it (that text
  parsed, not the layout in memory), in one atomic replace, so
  the same catalogs give the same bytes, a scene no SKU holds leaves the pool and a store the loader refuses
  is never written, and a corrupt store fails every SKU's fetch and is left as it is. `layout` refuses two
  spellings of one SKU (`h6006` and `H6006`, which it merged into one catalog: the union of both, or one the
  loader refused where both held a scene of one name), and a
  rewritten store is left readable by all (mode 0644; the temporary file it replaces the store with is created
  0600). Its naming (stable names, `scene_name`) and the lightEffect it takes for the SKU are unchanged, and
  it still stores nothing for a SKU the library has no scenes for. The store also holds `by_set` (review G1,
  always present, `{}` where nothing differs), which maps a SKU and a scene set (its versions sorted and
  comma-joined, `""` for the empty set) to the scenes listed differently there: `null` for one not listed,
  else the pool index of the entry the app plays there, an entry of the same name. The loader refuses an
  override of a name the SKU's catalog lacks, of a SKU without one, a ref to another scene's entry or to the
  catalog's own, a set key not in that canonical form, an empty map, and a pool entry that carries `by_set`
  (the shared entry check), and counts the overrides' entries against orphans. No bundled catalog has an
  override yet: `by_set` is `{}`.
- **`tools/fetch_scenes.py` reads a scene's values for the SKU, as the app does**
  (`CategoryV1.LightEffect`, `CategoryV1.java:323-422`): a `specialEffect` entry whose `supportSku` holds
  the SKU overrides the base code, scene type, param, param id, cmdVersion and big-effect flag (it took the
  base `lightEffects[0]` values only). The AppVersion header is
  now the traced app's, 7.7.02 (the scene library answers 7.5.20 identically for every bundled SKU; the
  catalogue endpoint does not, listing 15 more SKUs at 7.7.02, so `tools/fetch_catalog.py` no longer shares
  the constant and asks as its snapshot's `app_version` records: Spec), and `--resolve` logs in with it
  too (it used the cloud client's default, 7.5.20). The bundled catalogs are re-fetched with it in their
  own change (below), which changes 1.4.0's H6004, H60A6, H61A8 and H6641 catalogs (H6006 / H6008 keep
  1.4.0's).
- **`tools/fetch_scenes.py` keeps the lightEffect the app's scene list keeps** (devices.yaml
  `scenes.list_filter`; `SceneDataVM.b2` -> `CategoryV1.checkSupportNew` -> `SceneV1.checkSupport` /
  `checkVersion`, then `SceneDataVM.Companion.k`). Before it takes a scene's first lightEffect it drops each
  one whose op type for the SKU (`CategoryV1.getOpType`: scene type and cmdVersion) is outside the SKU's op
  set (a base sceneType 0 passes any), a DIY (op type 5) one without effect codes, and an expired one, and it
  skips an expired category. The op set is the screen's `ScenesConfigBean` `c`, which each scene row's
  `scene_versions` holds (Spec); the tool takes the union of the SKU's sets (the newest firmware's), and an
  empty set (a null `c`) filters nothing. The firmware is unknown at fetch time, so a scene whose first
  surviving effect has version `rules` is left out and named, and the `rules` flag is no longer written. A
  re-fetch no longer keeps an existing scene the library still lists but the filter refuses. Over the
  public library of 2026-10-19 the filter changes none of the 14 bundled SKUs' catalogs.
- **A bundled scene gated by version rules is refused**: `load_scenes` raises GoveeBleError (malformed) for a
  catalog with a scene marked `rules: true`, and `tools/fetch_scenes.py`'s `validate_entry` refuses one. The
  app lists such a scene only on the firmware its rules allow, which the library doesn't model, so offered
  it would show on every firmware (the loader used to drop the key). No bundled scene has one.
- **A scene placeholder is the app's own signal**: `tools/fetch_scenes.py` flags a scene `placeholder` only
  when it is a big-effect scene (`bigEffectStr`) with an empty param, which the app fetches before the apply
  (`BaseSceneViewMode.java:630`, `SceneDataVM.java:1324`), not when a param's byte 3 is 0xFF. That heuristic
  took H1401 / R1401's Christmas, a well-formed 82-byte 0x93 RGB scene whose frames were always sent, for a
  stub; its set loses the flag (`5cc121e05ebbda2d`, was `347134bb28e5895f`). The same flag on 1.4.0's H6004
  set (shared by H14B1) goes with the re-fetch (below).
- `tools/fetch_scenes.py` drops the characters the app's decoder skips from a scene blob:
  `android.util.Base64.decode(str, NO_WRAP)` (`Encode.decryByBase64`,
  `base-classes10/com/govee/base2home/util/Encode.java:29-31`) skips every character outside the alphabet
  and `=`. Four of H6087's library blobs carry a JSON-escaped `\/`, which the app reads as `/`, so that SKU
  fetches now. `--audit` reports a SKU that has a catalog but no device-table row as not supported instead
  of raising.
- **The bundled catalogs are re-fetched** with `tools/fetch_scenes.py` as it ships here, over the public scene
  library of 2026-10-19 (AppVersion 7.7.02, no `--resolve`), so each scene takes the values the app reads
  for its SKU (the `specialEffect` entry, above) and keeps its name, and the scenes the app has added are
  bundled. H6047, H6052, H6062, H1401 / R1401 and H6002 are unchanged, H6188's catalog is the app's own,
  not fetched, and H6006 / H6008 are not re-fetched (below). The store holds 1,010 scenes in 715 entries,
  its `by_set` still `{}`, and every catalog is pinned to its content (`tests/test_scenes_load.py`
  `RELEASED`).
  - H6006 / H6008 keep their 1.4.0 catalogs: on the bench on 2026-10-20 the 2026-10-19 library's codes did
    not play on them. Aurora at its new code, 1174, showed white on an H6006 (hardware 1.02.00), where
    1.4.0's 4019 plays it, and White Light as a plain activation of its new 1201 left an H6008 on its last
    scene. The app's per-device scene fetch is to be investigated in 1.5.1.
  - H6641: 92 of its 148 scenes take its own param id (White Light 17861, was 13890), 65 of them its own
    blob too, so those 65 upload other bytes. H61A8: 8 scenes take their own param id and blob (Flash, Gary,
    Judy, Nick, Sailboat, White Light, ZDP Duo, ZDP Trio). Both add Emotion's "Flash" (code 2020) as
    "Flash (Emotion)": Zootopia 2's (14769) keeps "Flash", as 1.4.0 bundled it.
    - H61A8's White Light (param id 13902, uploaded at its scene set {0, 1, 2, 3}): Live-verified on the
      bench 2026-10-20 (wire, read-back, seen: the planned upload and activation sent and acked, the device
      reporting its code, 10564, and the scene seen).
    - H6641's White Light (param id 17861): Live-verified on the bench 2026-10-20 (wire, read-back, seen).
    - H6641's Aurora (code 215, param id 17474): live-verified (wire and read-back) on the bench 2026-10-20;
      visual inconclusive.
    - Their other scenes, "Flash (Emotion)" among them: source-modelled from the 2026-10-19 library
      responses; not live-verified.
  - H60A6: a Natural "Lightning" (24448) is new: source-modelled from the 2026-10-19 library responses; not
    live-verified.
  - The library no longer lists Disney's Moana for H60A6. Its three scenes, Motunui, Heart of the Island and
    Wayfinding, stay under their names with their blobs, as scenes the library dropped (the tombstone's
    `dropped`), so an automation that names one still plays it.
  - H6004 / H14B1: Christmas loses 1.4.0's `placeholder` flag (the placeholder entry above); its frames were
    always sent. Source-modelled from the 2026-10-19 library responses; not live-verified.
- **`wire.build`'s read requests pass the spec's `read_request` its new profile param** (the library half
  of the AA 12 / 13 / B0 selector typing, Spec): the frame's profile, the plain one. No library read sends
  AA 12, 13 or B0, so no byte changes: every read frame of every supported SKU's rows, by default and with
  `allow_modelled`, is as before.

### Fixed
- **An H6047 bar write no longer sends a stale other bar.** One `33 36` carries both bars, and since 1.1.0
  `set_zone_power` read them (`aa 36`) only while a bar was unknown, so it sent the other bar's cached power
  however old: with the bars read {left on, right off} and the right bar switched on in the app a few minutes
  later, a left-bar write (Home Assistant powers a zone before its colour or `turn_on`) sent `33 36 01 00` and
  the right bar went dark. The app builds its `33 36` from bars its `aa 36` heartbeat keeps current (h6047
  `NewDetailVm.D5:669-720` from `Info4Detail.D` / `F`, which `NewDetailVm.A3` parseHeart `:574-612` sets on
  every `ComposeLightHeartController` reply). The library now reads the bars first unless the other bar's
  power was confirmed by the device (`DeviceState.zone_power_confirmed`) at most
  `const.ZONE_POWER_FRESH_SECONDS` (new, 120 s, the bound the Home Assistant integration uses) ago, and
  sends what it read. When that read goes unanswered or fails on a live link, a held other bar is sent as
  held, as the app sends the bars of its last heartbeat, and the write goes out as it did before (with its
  own reconnect-and-retry); a connect that fails raises, as before, without a second connect for the write.
  An unknown other bar still raises, as does a failed read while a bar is unknown. Not fixed: the held bar
  can be any age, where the app's comes from a heartbeat it repeats, so when that one read is lost a bar
  switched elsewhere since is still switched back, as before. This adds an `aa 36` read before such a write.
  Live-verified on the bench 2026-10-20 on an H6047 (firmware 1.00.12): with the right bar's confirmation
  aged to 125 s (past the 120 s bound), `set_zone_power('left', True)` read `aa 36` first and sent the right
  bar as read (`33 36 01 00`, acked), and the left bar came on.
- **A version a firmware-gated fact tests is read until it is in** (review G1). `Device.update()` takes the
  device-info read as done once the device answered, as before, but each later poll reads a version that a
  `scene_versions_by_firmware` gate or a Kelvin firmware range (`kelvin_by_hardware` /
  `kelvin_by_software`) tests and that the read did not bring back, alone (`aa 06` for the soft version,
  `aa 07 03` for the hard one), until it is in. One lost reply no longer holds those facts at the newest
  firmware's for the life of the `Device`: the H61A8's Kelvin range waited for its hard version for good.
  A SKU with no such fact reads neither again, as before.
- **`devices.device.make_device` routes by the advertised pact**, as `create_device` does: it passes the
  advertisement's pact to `profile_for`, so a pact the SKU's rows refuse gives None (it took the SKU's
  profile whatever the pact). Internal (not in `__all__`); no behaviour change without an advertisement.
- **A pact heard after the device is built that its rows refuse is logged**: `Device` keeps the profile it was
  routed to, and a later advertisement whose pact none of that profile's rows accepts (where the app's scan
  refuses the device or takes another row; `profile.refuses_pact`) logs a warning, once per change of pact.
  1.4.0's 18 SKUs at a pact no row lists keep their 1.x fallback and log nothing.
- **An advertisement that changes the pact notifies the callbacks**: `Device.ingest_advertisement`
  returns True if `state.is_on` changed, as in 1.4.0; the callbacks also fire when the advertised pact is
  first heard or changes (one notification when the on/off changes too), with the pact already stored. It
  learned the pact silently, so what the pact picks (`min_kelvin` / `max_kelvin` by `kelvin_by_pact` on
  H61F2 / H61F5 / H61F6, the brightness range and whether brightness can be written yet by
  `brightness_by_pact`, `scene_versions` by `scene_versions_by_pact`, the H60A6 0xAC status read's colour
  groups) reached a callback only at the next update. Any change of `Device.pact` counts, as it is itself
  exposed. `ADVERT_SUPPRESS_SECONDS` still holds back only the advertised on/off: a new pact inside that
  window notifies, and the on/off is left as it was. An advertisement without pact info, or with the pact
  already heard, notifies nothing; the on/off path and the return value are unchanged.
- **A wedged disconnect is abandoned at `DISCONNECT_TIMEOUT` even when the operation is cut short in its
  wait**: a teardown whose caller is cancelled while it waits (the operation deadline, or a poll's
  `connect_timeout`, on a failed connect at a wedged proxy) keeps running and is cancelled at the same bound,
  as an uninterrupted one is. It was left running for good, holding its bleak client. A poll's
  `connect_timeout` that runs out also waits out every teardown still running whose wait was cut short (its
  own, or one an earlier operation's cancellation cut short) to its bound before it raises and gives the
  device lock up, so the next connect doesn't start while an old link's disconnect still runs (a poll retried
  down that path stacked stale clients, which hold connection slots on an ESPHome proxy). A cancellation of
  that wait (the poll's caller, or the operation deadline) gives the lock up at once and leaves the teardown
  to its abandon at the bound, as at the defaults.
- **A superseded link's notifications are dropped**: each link's notify callback is bound to its client, and
  a frame from any client but the current one (a link whose teardown was cut short or abandoned can still
  deliver) is dropped with a DEBUG line, so it never reaches the next session's queue, where its handshake or
  first read took it. A superseded client's disconnect callback was already ignored.
- **A cancellation in the same tick as a reply is not lost on Python 3.11**: the reply wait is an
  `asyncio.timeout` instead of `asyncio.wait_for`, which on 3.11 returns a frame that arrives in the same tick
  as a cancellation of its caller (the operation deadline, a poll's `connect_timeout`, the caller's own) and
  swallows the cancellation (gh-86296). The semantics are now those of 3.12+ on 3.11.3 and later (3.12+
  unchanged); 3.11.0–3.11.2's `asyncio.timeout` has gh-102780, so in a task that carries a suppressed
  cancellation a reply wait that runs out raises `CancelledError` there. A frame already queued is still
  returned with no time left.
- **A colour report with no CCT test is not a Kelvin** (the nocct fold): `parse.mode_color` honours the
  layout's `is_cct` on the `[gradual][kelvin s16be]` decodes, so a `gradual_kelvin_s16be_nocct` report
  (mode_color_parse `kelvin_15_nocct`, `kelvin_0d_nocct`, ...) reads as `KelvinReport(0)`, not
  colour-temperature mode. Latent: no current SKU uses those decodes.
- **`tools/fetch_scenes.py` keeps every same-named scene, never points a stored name at another scene, and
  never gives a removed name to another.** A stored name is a Home Assistant effect name that automations
  keep. The app lists one sceneName in more than one category, or twice in one (H61A8 / H6641: Emotion's
  "Flash", code 2020, and Zootopia 2's, 14769; 41 of the 93 SKUs fetched on 2026-10-19, H1A42's "Sleep" and
  "Meditation" and H60B1's "Dusk" among them), and the fetch kept only the last of them. Each one is now
  stored: a new one under its sceneName where that is free, else as "<name> (<category>)" or "<name>
  (<category>, n)", with its sceneName in a new `scene_name` key where the two differ (the loader ignores the
  key). Of the new scenes of one sceneName the highest code keeps it, whatever the list order, so a first
  fetch names a response alike whatever the store holds (the bundled H61A8 / H6641 "Flash" is 14769, which a
  first fetch of H6168 picks too); no other SKU's stored names are taken (over the 93 SKUs fetched on
  2026-10-19 that changes no name). A re-fetch keeps the name of the stored scene of its sceneName that holds
  a listed scene's code and category (ties by the whole entry, a resolved one's as its placeholder, then by
  blob and param id; a placeholder has no blob, so it ties by its param id); else, only where exactly one
  stored and one listed scene fit each other, by one of its own lightEffects' codes or by category and blob
  (a scene the library renumbers keeps its name), printed as a move, or by category and param id or category
  alone, printed as a guess. A guess passes over a stored scene whose code the library still answers under any
  sceneName, or whose param id another listed scene carries (its scene, listed under another name), and a blob
  match over one it still answers at its own sceneName and category; both pass over one a fetch found gone
  from the library (its sceneName listed, or left out only for its time or rules, at its code under no
  category but where another stored or retired name sits), or found its scene (its param id) listed under
  another name or left out only for its time or rules at another code, since it was last listed, but a blob
  match with its param id too: that scene back at a new code, printed as a move (for example a scene whose
  category the library drops and later lists again at a new code with the same blob and param id; where more
  than one listed or stored scene fits so, the name stays dropped). Where more than one fits, the scene takes
  a new name, printed as ambiguous. Only a scene that matches none gets a new name, never one the store or the
  tombstone holds. A stored scene no longer listed keeps its name, as do one
  left out only for its time (an expired category or effect, its category renamed meanwhile too) or the
  firmware's version rules and one listed under another name or left out so at another code (its param id);
  a sceneName the app renames keeps its old name too, so two names hold one code. Only one whose scene the
  SKU's scene list refuses for good (its op set), or a sceneType-5 scene the library does not upload, is
  removed (where two stored scenes the fetch does not list could be that scene, neither is), printed, and
  retired in a tombstone (a SKU whose every stored scene is removed so leaves the store, its names retired:
  the store kept a catalog the cut emptied, unretired, and each re-fetch printed its moves again; a later
  fetch whose every listed scene is refused or cut says so, not that the library has no scenes),
  `tools/scene_tombstone.json` (out of the package; the store, the loader and the pool are unchanged), with
  the entry it held (one the fetch matched, then cut: the entry it was to hold): only that scene's exact
  return (sceneName, code, category and blob; a placeholder's with its param id) takes the name back, printed
  where the sceneType-5 cut keeps it, and one whose blob `--resolve` baked in returns so as its placeholder of
  the same param id, the blob coming back with the name. Scenes are named after the sceneType-5 scenes a
  `legacy_scene` SKU does not upload are left out, and a blob resolved by `--resolve` is kept while the
  library ships its scene as a placeholder of the same param id: under the name the scene keeps (through a
  renumber or a category rename too, with the new code and category), else by the scene's code and category,
  else by the resolved entries of that param id where they hold one blob (one scene under more names; a bare
  entry of it has no blob to keep, so the scene stays a placeholder, as a new name does where the sceneType-5
  cut would drop that blob). A library value the tool reads as an int (a lightEffect's or a `specialEffect`
  entry's `sceneCode`, `scenceParamId`, `sceneType`, `cmdVersion` or `bigEffectStr`, any SKU's, listed or
  not) that is not an int or null fails the SKU's fetch, with the store and the tombstone as they are: a scene
  type `1.9` or `"1"` read as one the SKU's list refuses removed and retired the stored name, and every name
  of a SKU whose every scene type was so, its catalog out of the store (the app's Gson fails the whole
  response on a bool or a fraction). A seeded property test replays 200 random histories and 64 fixed ones
  through `main()`. No bundled catalog changes, and no released name is gone (1.0.0 to 1.4.0), so the
  tombstone starts with no record. It holds `store_sha256`, the sha256 of the `store.json` it was written
  with: every write of the store (`--resolve`'s too) writes it, so `tests/test_fetch_scenes_store.py` fails a
  `store.json` committed without its tombstone (a fresh checkout would let a new scene take a retired name
  silently), and a run (`--resolve`'s too) over a tombstone that is missing or not the store's fetches and
  writes nothing: restore `tools/scene_tombstone.json` from git, or pass `--reset-tombstone` for a deliberate
  reset. A change of the tombstone alone (a name dropped) leaves `store.json` as it was, so a run that changes
  the tombstone ends by printing `tools/scene_tombstone.json changed (N records): commit it with this run`.
  With only `OUT_DIR` pointed elsewhere, the committed tombstone pairs with the bundled store and its
  `store_sha256` is left as it is.
- **A corrupt bundled scene catalog raises only GoveeBleError, and nothing in it is coerced.** A code JSON
  reads as an infinite float (`1e999`, `Infinity`) raised OverflowError, a store nested too deep
  RecursionError, and a corrupt `store.json` member of a zipped install (`importlib.resources` reads one)
  `zipfile.BadZipFile`, `zlib.error` or `lzma.LZMAError`, RuntimeError for a member flagged encrypted
  (NotImplementedError for one of an unknown compression method or flag), or EOFError for one that runs
  past the archive's end (entries that overlap at one offset, which zipfile only warns of on 3.12.14,
  3.13.15 and 3.14.6; it has no text, so the message names its type alone), past GoveeBleError (1.4.0's
  per-SKU files did too); the first two are malformed now (the deep one not kept, as a RecursionError never
  is), the zip errors unreadable. An unreadable store's
  error quotes its cause by its str, not its repr: for a store that is not UTF-8 the repr held the whole
  store, every blob in it (about 201,600 characters for one stray byte in the 194,010 B store as released,
  against about 140 now); `tools/fetch_scenes.py`'s error for a store or tombstone that is not UTF-8 held
  the whole file too, and now quotes its cause the same way, and a tombstone nested too deep is malformed
  (its RecursionError escaped). A scene name that is not a string is named by its type, not
  quoted (a name holding a blob put all of it in the error). The loader
  coerced mistyped values that `tools/fetch_scenes.py` refuses to write, or took them as they were: a code
  `"7"`, `1.9` or `true` read as 7, 1 and 1, a scene type `"2"` stayed a string and a placeholder `"no"` read
  as True. One entry check, `scenes._validate_entry`, is now the loader's and the tool's (`validate_entry`
  calls it), so the loader refuses exactly what the tool refuses: a name that is not a non-empty string, a
  code that is not an int from 0 to 0xFFFF (`build.scene_activate`'s range: a negative or larger code can't
  be sent), a param, category or `scene_name` that is not a string, a param id, scene type or cmdVersion that
  is not an int (a bool is not one), a placeholder or big-effect flag that is not a bool (each may be null
  or left out), a param that is not strict base64, and version rules. `tools/fetch_scenes.py` coerced such
  values from the library too: it took `int()` of a scene's code, param id, scene type and cmdVersion before
  the check (`1.9` and `true` were stored as 1, `"7"` as 7), a specialEffect's cmdVersion `or 0` (`false` as
  0) and a bigEffectStr `true` as 1 (a big effect). It takes the values as they are now, each an int or null
  (its check of the library's values, above, fails the SKU's fetch on any other); an int outside a Java int's
  range fails the fetch too, as Gson's `nextInt` fails the app's whole response on one (a scene type of
  `2**31` read as op type -1 removed and retired the stored name, and a param id of `2**31` was stored), and
  so does a `supportSku` that is not a list of strings (the SKU's `in` test took a string for a substring:
  `"H6641" in "H66411"`). The 93 responses fetched on 2026-10-19 carry JSON ints only, all small (10,611
  lightEffects and their 225,510 specialEffect entries; none has a bigEffectStr), and every supportSku is a
  list of strings. This fix changes no bundled scene: `load_scenes` and `scene_names_by_code` give what they
  did for every SKU, and `store.json` is unchanged by it.

### Spec
- `devices.yaml` `scenes.list_filter`: which library scenes the control screen lists and with which
  lightEffect (the op-set, DIY-code, version-rule and effective-time filters, the op-type table), cited.
  `devices.schema.json` `scene_versions` says what it holds: the `ScenesConfigBean`'s `c`, the op set the
  scene list is filtered by, which is also the upload versionArray where the bean has no apply set `e`
  (`ScenesConfigBean.j`). Every scene row's bean was checked: only H6052's has an `e` that differs, `{1, 3}`
  against `c` = `{0, 1, 4, 5}`, and its row's doc now says so (and that the library, as 1.4.0, only
  activates the type-3 graffiti scenes the app uploads over `e`); the `dispatch` source that called `c` its
  version array is corrected. A store test checks, at every scene set of each bundled SKU and with no
  exception, that every scene the library offers there has an op type in the set, that every scene it hides
  or replaces there is one whose catalog effect's op type is not, and that the pre-read set offers the whole
  catalog.
- `scenes.list_filter`: the bundled catalogs follow the rule at each of the SKU's scene sets (a catalog holds
  each scene's pick at the union, and per set where it differs, that the scene is not listed there or the
  lightEffect it plays there), a scene whose pick at one of its sets the library can't follow is not listed
  at that set, the list highlights the listed scene whose effect at position 0 has the reported code
  (`SceneListAdapter.java:155-159`; `SceneDataVM.i1` only picks the category page, `DelegateSceneV2.java:
  373-375`), and a client offers the catalog of the set in force. Cites
  `BaseSceneViewMode.java:466-477,591-697`. A null op set is an empty `scene_versions` (the H61A8 example
  is gone: its set is never null, below).
- **Row 19 (H61A8) cites the scene rule the app reaches** (review G1). `DeviceDetailConfig.k0` takes the
  first scene interceptor registered for a goodsType, and for 19 that is dreamcolorlightv2's
  (`RgbIcLightRegistry` registers `DreamColorV2Register` ahead of the chain whose dreamcolorlightv1 `$33`
  and `LightVersionServiceImpl.supportScenesOp` the row cited, which never run for 19). Its op set,
  dreamcolorlightv2 `Support.getSupportScenesOpSet`, is never null for 19 and has no pact gate: {0, 1, 2, 3},
  or {0, 1, 2} on a Telink hard version below hard 1.00.01 or soft 1.06.00, a test that is not a minimum
  version and that the row does not model (as for its brightness: the row holds for non-Telink hardware,
  such as the bench unit, SW 3.03.18 / HW 3.01.10). So `scene_versions` is [0, 1, 2, 3], 1.4.0's set, with
  no `by_pact` and no scene `by_firmware` (the row had the dreamcolorlightv1 rule: activate only below soft
  1.04.00 at pact code 1, {0, 1, 2} at pact code 2 or from soft 1.04.00 below part brightness); the H61A8
  sends what 1.4.0 sent at every pact and firmware. Not modelled, and in the row's doc: at pact (1,1) below
  soft 1.04.00 the app's scene tab shows its built-in static list (`DelegateScene4DreamColorLightV1V2UIV1`,
  `DeviceDetailConfig.l0`'s first match), and a version the app has not read, or that is empty, gives
  {0, 1, 2} there. The same bean gives 19's DIY version. `spec/govee_reference.py`'s self-test and
  `spec/docs/SCENE_UPLOAD_ENCODING.md`'s goodsType-19 row follow. A test checks that each row's cited scene, DIY
  or brightness interceptor is the first the app's lookup finds for its goodsType (the dreamcolor
  goodsTypes' registration order in `RgbIcLightRegistry`, and the app's one brightness interceptor); the
  dreamcolorlightv1 rows of goodsTypes 70 to 262 cite theirs.
- `devices.schema.json`: `clientModel` (`client_profile.by_model.<SKU>`) takes `kelvin_range` and
  `brightness_range`, and `scene_power_on` (the scene apply's power-on; bulblightv3 H6009 / H6010, cited in
  row 64's doc), which the generator refuses on a SKU whose row offers no scenes.
- `spec/govee_reference.py`: `client_row` takes the device's `sku` (keyword-only), as the library resolves
  a SKU: a Kelvin or brightness range the row lacks comes from the SKU's `by_model` entry, and its
  `by_firmware` Kelvin items, before the family `specs`. Without it, every result is unchanged.
- `devices.schema.json`: `ic_pieces` may be `fixed` (the IC count read at connect sizes nothing: the
  screen's piece count is fixed), and `ic_pieces` now requires the `ic_num` read.
- **The pact-conditioned reassignments' target rows list their SKUs and pacts**: the pacts the app
  registers for the target goodsType, cut to those at or above the item's gate. goodsType 40 (h6159,
  h6160, h6182) takes (2,1) (stringlightv2 `Support.addSupportPact`). goodsType 39 (h6113, h6114,
  h613839's H6138 / H6139, h6181, carlightv1, tvlightv1) takes (10,1) / (10,2), the same table. goodsType
  44 (h6119) takes (10,1) / (10,2) (h7090 `Support.addSupportPact`). goodsType 17 (barelightv1) takes
  (10,1) / (10,2) / (10,3) and not 17's (1,1) / (1,2) (dreamcolorlightv1 `Support.addSupportPact`). The
  `clientRow.pacts` description says so. None of these rows has `support`.
- **`spec/govee_catalog.json`: a dated snapshot of Govee's public product catalogue** (`GET
  https://app2.govee.com/bi/rest/devices/v3/skus`, the app's `ICategoriesApi`: an AppVersion header, no
  account; fetched 2026-10-19 with AppVersion 7.7.02, the traced app's, which its `app_version` records). It
  holds 632 SKUs, each with its goodsType and ic only (no names, images or URLs). Its doc cites how the app
  uses them.
  - It was first fetched on 2026-10-18 with AppVersion 7.5.20 (617 SKUs). The 7.7.02 refresh adds 15 SKUs
    and changes or drops none. **R14B1** (408) gains a route (Added).
  - **H14B1** (408), **H1AA2** (403) and **H1AA5** (404) are listed by their rows at the same goodsType.
    Their docs no longer say the snapshot lacks them.
  - **H1880** / **H3860** (h6092 402 / 401), **H3010-H3013** (h7020 411) and **H6250** (h6609 398) are
    listed by rows without support or pacts, so they stay unrouted. **H3700** (383), **H5210** (407),
    **H6821** (280) and **H8150** (397) have no row.
  - The review list (H615E) and the unrouted set (empty) are unchanged.
  Every catalogue goodsType goes into the app's SKU → goodsType map (`CategoriesDataManager.n` →
  `Pact.j`), which a scan reads before the built-in table (`Pact.f` → `Constant4L5.hasKnowSupportGoodsType`,
  default 0). goodsType 0 is the legacy code: a scan at 0 parses no advertised pact and re-derives the
  device by SKU (`BaseBleProcessor.c` / `.g`), so a catalogue 0 never routes. A devices.yaml row that lists
  a SKU wins, and a non-zero conflict is settled only by an `assigned_goods_type` item that cites the app's
  rewrite. ic is the app's default IC count (`CategoriesConfig.getDefIcNum`). The device table takes its
  routes (Added). `tools/fetch_catalog.py` refreshes it from the endpoint or a saved response, and rewrites
  only `app_version`, `fetched` and `skus`. It asks with the snapshot's own `app_version` unless
  `--app-version` names another, since the endpoint's answer depends on it (on 2026-10-19 it listed 617
  SKUs at 7.5.20). It is dev-only and standard-library, reuses `fetch_scenes.py`'s request handling, and the
  build never runs it. The generator refuses a snapshot with a top-level key other than `app_version`,
  `doc`, `doc-ref`, `fetched`, `skus` and `source_app`, an `app_version` that is missing or not in the app's
  version form, a `fetched` that is missing or not an ISO date (as `fetch_catalog.py` writes it), or an entry
  whose keys are not exactly `goodsType` and `ic`, so no name or URL can get in.
- **devices.yaml can now route and describe the 18 supported SKUs on its own** (one-device-list phase 0).
  Each one's goodsType has a `client_profile.by_goods_type` row with the facts the library's hand table
  holds: `models`, `pacts` (the accepted pacts, `Support.addSupportPact`; `any` for the SKU-routed H6188),
  `support` (`hardware_verified`, `app_traced` or `modelled`), `capabilities`, `zones`, `account_lock`,
  `scene_dialect`, `scene_versions`, `kelvin_range`, `brightness_range`, `status_read` (the 0xAC request),
  `connect_reads` (the state reads besides aa 01 and aa 05), `connect_writes`, `power_resync`, `ic_pieces`
  and `mode_reports_kelvin`, with `doc` and `doc-ref`. The rows are 303 (H60A6), 119 (H6047), 19 (H61A8),
  64 (H6004 / H6006 / H6008), 22 (H6052), 247 (H6641), 0 (H6188), 41 (H6062), 51 (H6061) and the plugs'
  43 / 50 / 90 / 307. The trace notes in `devices/profile.py` are now the rows' `doc` / `doc-ref`.
- **Seven SKUs as modelled rows first** (one-device-list phase 2): rows for goodsTypes the spec already
  describes, added as `support: modelled` (source-modelled, not live-verified: no unit was on the bench).
  The app trace below makes all seven `app_traced`. Each row's `doc` names what is not traced.
  - pact_h6099 203 (**H6098 / H8098**, pacts (1, 1) / (2, 1) / (3, 1)) and 336 (**H6690**, pact (1, 1)),
    one profile: the h60a6 0x15 frames, 14 segments by the direct `aa a5` groups of 4, Kelvin
    [2000, 9000], `kelvin_15` colour report. The app's colour strip sends 15 04 groups where the library
    sends per-colour 15 01 masks (documented).
  - h7040 36 (**H7040**, pact (1, 1)): whole-device 0x0d colour and CCT (the h6006 scheme), Kelvin
    [2000, 9000], `rgb_kelvin_0d` colour report.
  - Three status-read rows, each read with its own wire facts (`DeviceProfile.status_decode`) and
    requesting `ac 03 02 41 a5`, with a `gradual_15` mode report: h60b1 300 (**H60B1**, pact (1, 1)): the
    h60a6 0x15 frames over 3 pieces, colour groups counted from the TLV len; h7056 304 (**H7056**, pact
    (1, 1)): 8 pieces in fixed groups of 4; h1630 364 (**H1630**, pacts (1, 1) / (1, 2) / (1, 3)): one
    piece, Kelvin [1000, 10000], its 0xA5 gradient record not decoded.
  Their scene dialect is now `legacy_scene` (the new-DIY rule below).
- **41 more SKUs from the targeted app trace** (one-device-list, the admit commit): `client_profile`
  rows for the goodsTypes the trace completed, each with its accepted pacts and its control-screen facts,
  cited in `doc-ref`. Admitted by default (`app_traced`, not hardware-verified):
  - bulblightv3: **H6005** (20), **H6002** (31, scenes only);
  - h61d3: **H6640** (242), **H61B3 / H61B6** (264), **H1A43 / H1A44** (367), **H1B6A** (384), **H1AB1**
    (387), **H1AB2** (388), **H1AB3** (389), **H1A42** (400), **H1AA2** (403), **H1AA5** (404);
  - dreamcolorlightv1: **H617C / H617E / H617F** (70), **H617A** (73), **H618A** (74), **H619A** (75),
    **H619Z** (83), **H6168** (136), **H61BE** (175), **H6176 / H61BC** (176), **H61BA** (178), **H6175**
    (187), **H616C** (207), **H6169** (262);
  - **H610B** (striplight 114), **H6042** (h6047 197), **B7080 / B7081 / B7082** (h7080 45 / 55 / 56),
    **H6087 / H6088** (h7050 77 / 157);
  - phase 2's modelled rows, now traced: **H6690** (pact_h6099 336, with gradual), **H7040** (h7040 36),
    **H60B1** (h60b1 300), **H7056** (h7056 304) and **H1630** (h1630 364);
  - the catalogue snapshot also routes five SKUs no row lists to these rows, admitted with them by
    catalogue goodsType alone (not bench-checked): **H6167** (dreamcolorlightv1 83), **H616D / H616E** (207), **H802A** (74) and **R1630**
    (h1630 364, which leaves `allow_modelled` with its row).

  Behind `allow_modelled` (source-modelled, not live-verified): **H61B8 / H61B9** (h61d3 299), and
  **H8015** (bulblightv3 328, colour temperature only). 328's facts are traced, but its illumination tab, the
  only colour-temperature control, writes the Kelvin frame and `33 04` with the current brightness as one
  write (`View4IlluminationMode.I0`), where `Device.set_color_temp` sends the Kelvin frame alone, so the row
  is held (`support: modelled`) until the library sends that pair. The
  H6042's wire piece count is now the control screen's 5,
  not 10. The 18 SKUs of 1.4.0 are unchanged.
- **24 more SKUs on the phase-3 library changes**, all `app_traced`:
  - bulblightv3 111 (**H1501 / H601A / H601B / H601C / H601D / H801A / H801C / H801D / R1501**), 348
    (**H1401 / R1401**) and 408 (**H14B1**): the light connect write 33 25 01 (`connect_writes:
    [light_effect_stop]`); 348's Kelvin range is [1000, 10000] from soft version 1.00.15, else
    [2700, 6500]. So an H1401 / R1401's range widens from [2700, 6500] (valid on every revision, until the
    soft version is read) to [1000, 10000] after the first poll's `aa 06` read on such firmware, as the
    app's slider does. `Device.min_kelvin` / `max_kelvin` follow it. In Home Assistant, until the
    integration refreshes its entity's range after that read, the colour-temperature slider keeps the setup
    range [2700, 6500], while service calls and the reported state can go past it;
  - h7050 46 / 76 / 105 / 107 / 113 / 116 / 171 / 218 / 219 / 227 (**H7050 / H7060 / H7051 / H7062 /
    H7065 / H7055 / H7066 / H7052 / H7053 / H7063**): the `aa a3` connect read without the gradual control;
  - pact_h6099 203 (**H6098 / H8098**), promoted from `modelled`: the catalogue snapshot routes H8098 to
    goodsType 203, and the app's pact_h6099 `MatterSupport` ties them in code too.

  Behind `allow_modelled`: h61d3 261 (**H61F2 / H61F5 / H61F6**), Kelvin [1000, 10000] at pact (4, 1), else
  [2700, 6500]; its piece count has no app cap, so the mask width is modelled, as on 299.
- **One scene dialect for the goodsTypes that register a new-DIY parser.** On such a goodsType
  (`DiyNewConfig.addDiyEffectParse`), the control screen sends a sceneType-5 scene whose param leads 0x50
  through that parser (`BaseSceneViewMode.r3`) and every other scene through `parseSceneV1`, as on 64, 348
  and 408, which were already `legacy_scene`. The rows held `untraced` for that registration alone are now
  `legacy_scene` too, so they offer scenes once their catalogs are bundled: h6047 197, bulblightv3
  111 / 20, h61d3 242 / 264 / 299 / 367 / 384 / 387 / 388 / 389 / 400 / 403 / 404 / 261, dreamcolorlightv1
  70 / 73 / 74 / 75 / 83 / 136 / 175 / 176 / 178 / 187 / 207 / 262, h60b1 300, pact_h6099 203 / 336, h7040
  36, h7080 45 / 55 / 56, h7056 304, h7050 77 / 157 / 46 / 76 / 105 / 107 / 113 / 116 / 171 / 218 / 219 / 227
  and h1630 364: 70 SKUs (5 of them behind `allow_modelled`). striplight 114 (H610B, its own scene view
  model) and h6061 51 stay `untraced`. The library uploads no sceneType-5 scene under `legacy_scene` (it only
  activates one), so a `legacy_scene` SKU's bundled catalog holds none that the app uploads: none whose param
  leads 0x50, and none at all where its scene versions hold one of `parseSceneV1`'s sceneType-5 branches (7
  to 13: H1630's 13). `tools/fetch_scenes.py` leaves such scenes out, and a test checks every bundled
  catalog. 1.4.0's catalogs hold no sceneType-5 scene outside H60A6 and H6052, which have their own type-5
  dialects, so nothing they send changes. Rows 64, 348 and 408 now cite `BulbDiyConfig.a` / `.b` (348 does
  have a new-DIY parser).
- **H6004 routes to goodsType 64.** Rows 9 and 10 (H6003, and the H6004's Wi-Fi revision) say
  `connectivity: [wifi, iot]`, so no BLE client routes there.
- **goodsType 64's scene versions are {0, 1}**, the control screen's `ScenesConfigBean` set
  (`Support.supportScenesOp(64)`). The `{1}` of `Support.is2NewScenesMode` is the IoT / group path, now
  `scenes.dispatch.iot_apply`. `scenes.dispatch.family_apply` (a per-SKU table that repeated the rows and
  contradicted the control screens) is gone, and H6052's type-5 path moved from `scenes.dispatch.bypasses`
  to `scenes.upload.dialects.tablelamp_h6052_type5_diy`.
- **One pact and firmware branch mechanism per row.** `mode_color_by_pact` / `mode_color_by_firmware` (14
  and 4 sites) and A8's `kelvin_by_firmware` fold into general `by_pact` / `by_firmware` branches on
  `client_profile` rows (`clientBranch` / `clientFirmware`); the values are unchanged. Every matching
  firmware item applies, in order, since the app's gates are independent per fact (the dreamcolorlightv1
  rows' scene sets, from part brightness, and goodsType 19's Kelvin range). The TV-light pact (1,1) branch's nested firmware gate
  is now the row's own `by_firmware`, with the same results.
- **Schema completeness check** (`devices.schema.json` `clientRow.allOf`): a row with `support` must carry
  its routing and control facts, a row offering scenes its dialect, a plug row `power_resync`, and an
  IC-count read its `ic_pieces`; `doc-ref` items must be in the `.ksy` citation form. Family `specs` and
  `pact` lose the values that were one SKU's or that repeated `specs` (`pact.scene_versions` in 9 families).
- `spec/govee_reference.py`: `ble_rows` (the BLE route for a SKU and advertised pact) and `client_row` (one
  row's facts for a pact and firmware), with self-test checks. The Kelvin gate (a `kelvin_range` item's
  `min_hard_version`) is a string compare, as the app's `KelvinConfig.Config.z2` and the library's, in
  `client_row` and the `clientFirmware` description: hard 10.00.00 is below 3.08.01.
- Row 247 (H6641) lists the gradual flag its screen reads at connect and writes (`capabilities` and
  `connect_reads`); row 51 (H6061) names its colour scheme, `h6061`. The plug rows 50, 90 and 307 and row 19
  (H61A8) carry the citations the hand table had (`UiV2` / `UiV4` / `Ui4H5089.onOffChange`, the
  `/rgbic/newdetail` route and `SubMode4Color`'s parse), and the plug rows say app-traced, not
  source-modelled.
- **dreamcolorlightv1's family `color_scheme` is `h60a6`** (was `h61a8`): the 0x15 frames that every
  goodsType of the family but 17 / 18 below their gates sends. The app's colour `Config.x` is 0 for 19, 70,
  71, 73, 74, 75, 83, 123, 136, 178, 187, 193, 204, 207 and 234; 175 / 176 / 262 have no entry and take
  version 0 too. The 0x0b frames come from `Config.x` for 17 / 18 below their gates, and from the SKU map
  `Config.w`, which the app reads first, for the old H6102, H6163 and H6117 by their firmware. Every row
  already names `h60a6`, so no row's facts change: the resolved device table is unchanged, and the
  generated one differs only in that family default.
- **goodsType 123 is not a 0x0b goodsType**: its `Config.x` entry (`Bv`) is 0 for every device, as
  `Support4DreamColorV1.h` is true for every goodsType but 17 / 18 / 19. The dreamcolorlightv1 wire doc, the
  `wire.build` colour-scheme comment and 1.4.0's notes listed it with 17 / 18. The wire doc now gives the
  family's 0x0b senders (17 / 18 below their gates; H6102, H6163 and H6117 by their firmware). The other two
  give the app's whole list: by goodsType, 3, and 13 / 17 / 18 / 21 below their gates; by SKU, H7022 /
  H7024, and the old H6102, H6107, H6116, H6117, H6125, H6126, H6127, H6161 and H6163 by their firmware.
- **H1630 / R1630 (h1630 364) read the 0xAC status TLV 0x41 as the power-off memory** (`f_41:
  power_memory`, as on H60A6, H60B1 and H7056). The app's status lambda stores the value's byte 1 with no
  guard (`VM4LightH1630$afterConnectedSingleReadDeviceInfo$1.o`, into `Info4Detail.r`, the field
  `Info4Detail.N` fills from an `aa 41` reply). So a status reply whose 0x41 value is 1 byte long, or empty
  (but as the stream's last two bytes, which the app's walker does not read as a TLV), now fails the read, as
  the app's does: it gives no state, the poll retries once, and then power, brightness and mode stay as they
  were. A 0x41 value of 2 bytes or more reads as before, and the library still uses nothing from it. The
  0xA5 gradient record's own failure (`Controller4ColorInfoByGroup.r` reads 11 bytes) is not flagged yet, as
  the record has no wire form. No other SKU's profile changes.
- `read_request` takes the profile and types the AA 12 / AA 13 / AA B0 selector per form (rule 6):
  `timer_count_query` [group] (FIRMWARE-SOURCED on H5083, whose screen sends none),
  `plug_timer_page_query` [group][page] and `plug_delay_query` [outlet][delay_type]
  (H5083NewDetailRepository.loadParser:206-209, necessaryMessages:326-336). Any other form leaves the
  selector in rest. ProType 0x3F and 0x66 get FIRMWARE-SOURCED, CAUTION bodies, traced on the H5083, H6006,
  H6008, H60A1 and H60A6 shared write layers and never sent: `matter_packet` / `matter_start` (the 20-byte
  Matter-factory units, START op a plain byte since the ops differ per image) with the `matter_result`
  [op][status] reply, and `factory_test_request` / `factory_test_reply` (`factory_test_op`: 66 01 logs and
  is answered 66 01 00, 66 02 is echoed, and H6047 echoes every 66 frame). `matter_result.status` gives
  each op's results: ops 1, 2 and 4 give 0, 1 or 2; op 3 (the gid set) always 0, even when it stores
  nothing; op 5 (the encrypted gid, H5083 and H6006) 1 on H5083 and 0 on H6006 for a length over its
  limit, 0xFF on a gid MD5 mismatch, else the low byte of the AES ECB init or crypt error or of the gid
  store's result. `matter_packet`'s doc and FIRMWARE_VALIDATION give the per-image 3F length rules.
  `firmware.yaml` is unchanged while the simulator is paused: it still reads the H5083 timer, page and
  delay selectors as raw bytes, the same bytes the typed fields read, and its `factory_test_start` and
  `multi_write_3f` notes still say 0x66 and 0x3F have no spec body type.
- **The h5080 timer forms cover the family, not just H5083.** `f_12: timer_count` (AA 12 [group] ->
  [group][count], the group being the outlet) is the family's: every h5080 screen sends the same count read
  (NewTimerCountController from BleOpV2.o0, BleOpV4.l0 and BleOp4H5089.V / Y; the H5083 screen sends the same
  bytes, H5083NewDetailRepository.necessaryMessages). `f_13: plug_timer` (the AA 13 [group][page] page read,
  its reply and the 33 13 add) goes on goodsTypes 43, 50 and 90: every goodsType-43 SKU (H5080, H5083, H5085)
  opens the H5083 screen (Model.t), and the H5082 and H5160 / H5161 screens send the same [outlet][page] read
  (NewTimerControllerV2.p). The H5089 (307) keeps `f_13` unknown: its page read packs [group << 4 | page] into
  one byte (Controller4H5089TimerInfo.p), which `plug_timer_page_query` doesn't type. Some firmware is read
  with AA B4 instead of AA 12: the H5083 screen on H5080 soft 1.00.08 and below (Support.isOldH5080), and the
  H5082 screen on its connect reads until `UiV2.M`, at info-over, finds soft 1.00.11 or above and switches to
  AA 12. The timer types' docs say h5080 where they said H5083, and keep H5083's firmware facts as H5083's.
  The library uses neither form, so no frame or decode changes, and H5083's forms are as before.

## [1.4.0] — 2026-10-18

### Added
- `DeviceState.device_id`: the id the Govee app calls `device` (e.g. `2D:DB:5C:E7:53:F4:74:56`), read
  with `aa 07 02`. An alias of `serial_number`, which holds the same value.
- `build.soft_version_query` (`aa 06`), `build.INFO_HARD` (`aa 07 03`) and `parse.parse_soft_version`.
- New exceptions, defined in `govee_ble_local.exceptions` and exported in `govee_ble_local.__all__`:
  `GoveeBleCommandRejected` (the device acknowledged a write with a non-zero status),
  `GoveeBleHandshakeTimeout` and `GoveeBleHandshakeConnectionError` (both a `GoveeBleHandshakeError`, and
  also a `GoveeBleTimeout` / `GoveeBleConnectionError`), and `GoveeBleUnknownName` (an unknown zone or
  scene name; both a `GoveeBleInvalidValue` and a `GoveeBleNotSupported`).
- `GoveeCloudAuthError` (a `GoveeCloudError`): the Govee account rejected the email/password (HTTP 401/403,
  or a 401/403 `status` in the body), or rejected a saved token when there are no credentials to log in
  again, so an integration can tell bad credentials from connectivity problems. Both cloud errors now live in
  `govee_ble_local.exceptions` and are exported from `govee_ble_local` (catchable without the `cloud` extra)
  and from `govee_ble_local.cloud`; `govee_ble_local.cloud.account.GoveeCloudError` still imports.
- `Device.snapshot()`: a copy of the device state that later changes don't touch, to keep or compare;
  `Device.state` stays the live object.
- `GoveeAdvertisement.pact` (`(pact_type, pact_code)`, or None when the advertisement carried no pact info)
  and `GoveeAdvertisement.encryption_known`, so callers no longer test the `-1` sentinel; and
  `identify.from_advertisement()`, which identifies a bleak advertisement (or an already-parsed one) the
  way `Device` and `scanner.match` do.
- `scenes.async_load_scenes(sku)`: `load_scenes` without blocking the event loop (the first lookup per SKU
  reads the bundled JSON in a worker thread; later ones are cached).
- `transport.framelog.flush(timeout)`: waits, up to `timeout`, until the frame-capture writer has written
  every queued record, and returns whether it drained. It blocks, so call it off the event loop.
- `DeviceProfile` fields, keyword-only and defaulted: `relay_on_mask` and `advert_on_mask` (which bits of a
  plug's relay reply and of its advertised switch byte mean "on"; their defaults, 0xFF and None, keep
  1.3.0's rules of any relay bit and an advert byte of 1, so custom profiles behave as before),
  `color_readback_ic_range` and `color_readback_ic_per_piece` (the live IC count's clamp and divisor), and
  `mode_color_parse` (how the `aa 05` colour sub-modes are decoded, typed `devices.profile.ModeColorParse`:
  `"rgb_kelvin"`, the default, `"rgb_kelvin_tint"`, `"rgb_0d"`, `"kelvin"`, `"kelvin_15"` or
  `"rgb_kelvin_0d"`). `identify.parse_broadcast_onoff` gains a keyword-only `on_mask`; the default keeps the
  lights' `== 1` rule.
- `DeviceProfile.kelvin_by_hardware` and `DeviceProfile.mode_reports_kelvin`, keyword-only and defaulted:
  - `kelvin_by_hardware` holds Kelvin ranges by hardware version, highest version first, compared as
    strings as the app does. `Device.min_kelvin` / `max_kelvin` give the range in force: until `aa 07 03`
    is read, the range valid on every revision. A profile must give both base ends, and each range must
    overlap the base.
  - `mode_reports_kelvin` says whether the `aa 05` report carries a colour-temperature write's Kelvin. It
    is False on the H61A8 (see Changed, A8).
- Helpers: `wire.parse.parse_mode_rgb_kelvin_tint`, `parse_mode_0d_rgb`, `parse_mode_0d_for(frame, scheme)`
  and `SUBMODE4COLOR_SUB_MODES`, and the `wire.parse.GroupRecord` type; `wire.reassemble.burst_chunks`
  and `read_status_stream` (the 0xAC completeness rule and the status stream's root read, shared by the
  runtime and `govee-ble-analyze`); `wire.describe.status_burst_report` / `StatusBurstReport`, with `truncated` and
  `redacted` counts, and the `wire.describe.Direction`, `Severity` and `Issue` types; and
  `crypto.FrameCipher`, the frame cipher bound to one session key with its key setup done once (exactly
  the bytes of `crypto.encrypt` / `crypto.decrypt`).
- Constants: `const.ADVERT_SUPPRESS_SECONDS` (15 s; moved from `devices.device`, still importable there),
  `const.DISCONNECT_TIMEOUT`, `const.QUERY_TERMINAL`, the parts `LOCAL_NAME_PREFIXES` is built from
  (`const.BRAND_MARKERS`, `GBK_PREFIX`, `GV_LONG_PREFIXES`, `GV_PREFIX`; the tuple is unchanged),
  `wire.build.PRO_NOTIFY` (0xEE), `wire.build.PRO_MULTI_A1` (0xA1), `wire.reassemble.TYPE_MODE`, and the `transport.framelog.Direction` type.
- `wire.reassemble.parse_status(frames, *, pieces=None, decode=None)`: `pieces` is the device's colour-piece
  count, which sets how many records each 0xA5 colour group holds (see Fixed); omitted, it keeps 1.3.0's
  reading (the count comes from each TLV's length). `decode` is the device's wire profile
  (`devices.profile.wire_profile`), whose colour decode reads the 0x05 mode value; omitted, it is the H60A6's
  (see Changed, rule 4 c).
- `wire.build.PRO_MULTI_A2` (0xA2, `multi_read`), `wire.describe.chunk_redacted` and
  `devices.brightness.percent_to_device` (see Fixed).
- `DeviceProfile.relay_power` (keyword-only, typed `devices.profile.RelayPower`: `"outlet0"`, the default and
  1.3.0's `33 01 11 / 10`, `"all_outlets"` or `"total_switch"`) and the builders `wire.build.plug_all_outlets`
  (`33 01 FF / F0`) and `plug_total_switch` (`33 02 01 / 00`), with `RELAY_ALL_ON` / `RELAY_ALL_OFF` and
  `CMD_TOTAL_SWITCH`; the `ModeColorParse` value `"kelvin_15_rgb_kelvin_0d"`; and
  `wire.reassemble.StatusReply.mode`, the 0xAC reply's 0x05 TLV (see Changed).
- `DeviceState.last_color_temp_kelvin`: the colour temperature the device last reported or was set to
  (> 0), kept while it is in another mode, as the app keeps its Kelvin. `color_temp_kelvin` still means
  "in colour-temperature mode" and is cleared as before, so Home Assistant's colour mode is unchanged. A
  report with a Kelvin of 0 or below (H60A6, H6062, H6641, H61A8) keeps it, as `SubMode4Color` takes a
  Kelvin only when it is > 0 (`OpInfo4Detail.parseModeValidBytes:766-778`; pact_h60a0 h60a1
  `Info4Detail.Q:399-406`); on H6047 it follows the bars only while both report the same Kelvin (h6047
  detail `Info4Detail` ctor strategy :173-185). Writes set it too. Modeled from the decompiled app. On the
  H61A8, whose report carries no Kelvin, `color_temp_kelvin` stays as well (`mode_reports_kelvin`).
- `wire.parse.parse_light_power`: an `aa 01` reply as a light reads it, on only for a byte of 1 (the spec's
  `switch_read_reply.light_is_on`); `parse_power` keeps the raw byte for the plugs' relay mask.
- `GoveeConnection(session_frames=...)`: writes sent once per connection after the unlock, whose refusal
  or missing ack is only logged; `GoveeConnection.write_payload_size`, the link's write size (ATT MTU - 3,
  20..509, 20 when the backend can't say); `send_unacked` also takes a function of that size.
- `wire.parse.request_command(frame)`: the command a built `aa` read request asks for (the frame reader's
  `read_command.command`), which is the command its reply carries; and `wire.parse.IcCount.ic_count_checked`,
  the IC count as the app's checking readers return it (the spec's `ic_count_checked`: None for a count of
  0x1000 or more).
- `wire.reassemble.BurstAssembler`: the app's 0xAC burst state machine, fed one record at a time
  (`feed` returns whether the app takes it; `status` is `"waiting"`, `"complete"` or `"failed"`).
- `DeviceProfile` fields, keyword-only and defaulted: `power_resync` (gt 50 / 90 plugs, see Fixed) and
  `status_color_groups_pact` (the pact the 0xAC status read needs before it asks for the 0xA5 colour
  groups). `scenes.scene_upload_frames` and `Device.scene_upload_frames` take a keyword-only `mtu`
  (default 20, today's frames).

### Changed
- **The H60A6 status read no longer fails on its empty mode value (rule 6, FIRMWARE-SOURCED; library half of
  cd8acdc, which is 6da26e0 rebased).**
  - In music mode (0xA0), and in every mode but 04 / 0a / 0d / 13 / 15, the H60A6 SW1.00.41 sends an empty
    TLV 0x05 (`[05][00]`) in the middle of its 0xAC burst, on purpose (FUN_9b025dc8 @0x9b025dd2-0x9b025ddc).
  - The app's whole read fails there (Info4Detail.Q reads a first byte), so its power and brightness go
    stale. Without this change, the library's read did the same.
  - `parse_status` now reads past that one value (`govee_state.app_empty_mode_fails`): the mode stays
    unknown (`StatusReply.mode` None, the state's mode left as it was), and the rest of the burst applies.
  - Every other `app_read_fails` value still fails the read, a mode value short of its layout included.
  - The analyzer no longer lists the empty value as a spec gap.
  - Decided by the user: "I do't want to drop the [05][00] tolerance, that seems like a bug in the app to
    me".
- **H61A8 sends the app's 0x15 colour frames and gains colour temperature and per-segment brightness (A8;
  library half of 1beeb75).**
  - The goodsType 19 colour page sends the h60a6 scheme's 0x15 forms (Config.x[19] = 0 -> Config.Yt):
    Bytes.h for RGB, and Bytes.r for a Kelvin or for an exact Kelvin tint picked as a colour. The 0x0b frame
    is never sent.
  - The H61A8 profile moves to `color_scheme="h60a6"` and gains COLOR_TEMP. Its Kelvin range is [2000, 9000],
    or [2700, 6500] from hardware "3.08.01" (KelvinConfig.Config.z2, a string compare). Until `aa 07 03` gives
    the hardware version, the range valid on both, [2700, 6500], applies.
  - Per-segment brightness now works, as `33 05 15 02` (Bytes.G).
  - **Fixes a live bug:** today's `set_rgb` on the bench H61A8 (SW 3.03.18, HW 3.01.10) is rejected, because
    the device answers the 0x0b frame with status 01. The new RGB and 2700 K frames are byte for byte the
    ones acked 00 on the bench on 2026-10-17, and both recoloured all 15 segments.
  - After either write the device's `aa 05` reads `15 01 00 00`, so the report carries no Kelvin. On the
    H61A8, a colour report of Kelvin 0 or below now leaves the Kelvin the library sent. This is a library
    state rule and changes no frames. Only an RGB or scene write, or a scene report, clears it; other
    sub-modes leave it, as on every SKU. Because the H61A8 reports `15 01 00 00` after an RGB write too, a
    Kelvin the library sent stays in state across a colour change made in the Govee app.
  - The range and the report rule are the new `DeviceProfile` fields `kelvin_by_hardware` and
    `mode_reports_kelvin` (see Added). An RGB pick reads the range once, so the frame and the state agree
    even if `aa 07 03` lands mid-write.
  - The `h61a8` scheme stays for custom profiles; no built-in profile uses it now. It is the app's 0x0b
    colour instruction 3, used by goodsType 3, by goodsTypes 13, 17, 18 and 21 below their gates, by H7022
    / H7024, and, depending on their firmware, by the old H6102, H6107, H6116, H6117, H6125, H6126, H6127,
    H6161 and H6163. Removing it is left for a major release.
  - Bench, 2026-10-18 (same unit): `set_segment_brightness([0..4], 50)` sent `33 05 15 02 32 1f 00 ..`, the
    app's Bytes.G, which was acked 00. The read-back changed only those segments' brightness, with no colour
    changed, and the start state was restored exactly.
  - Per-segment colour temperature is hardware-verified on the same unit (bench, SW 3.03.18 / HW 3.01.10,
    2026-10-18). `set_segment_color_temp([0..4], 5000)` sent `33 05 15 01 ff ff ff 13 88 ff e7 cc 1f 00 ..`
    (Bytes.r with a partial mask), and `set_segment_rgb([5, 6, 7], ...)` with 4000 K's tint sent it as
    Bytes.r under the [2000, 9000] range. Both were acked 00. The read-back set exactly the masked segments
    to the tint and left every brightness alone, and the start state was restored exactly. Not
    app-traced.
  - Not live-verified (this unit is HW 3.01.10): the [2700, 6500] range of hardware 3.08.01 and up, which
    stays source-traced, and a Kelvin outside [2700, 6500].
- **The 0xAC status burst is read through the spec's `govee_status_stream` root (rule 4 c).**
  - `parse_status` reads the joined burst in one go through `govee_status_stream`, under the H60A6 status
    screen's profile: zone bits, power memory and colour groups, 0x40 / 0xA3 unknown, the device's colour
    decode, and Info4Detail.Q's V2 music layout and empty-value rule. The new `read_status_stream` returns
    that read and where it stopped.
  - The read fails where the app's does: a stream that doesn't read, or a value the spec flags
    (`status_tlv.app_read_fails`). A colour group is still judged by its piece count.
  - The 0x05 TLV's `mode_report` comes straight from that read, with the device's colour decode
    (`parse_status(..., decode=wire_profile(profile))`); without a `decode` (1.3.0's call), the H60A6's, the
    one device whose readback is this read. `StatusReply.mode` carries it and is left out of equality.
  - A switch, brightness, zone or device-info TLV with no value to read fails the read, even where the spec
    doesn't flag it, rather than raising out of the device's poll.
  - Removed (all unreleased): `walk_status_tlvs`, `TlvCut`, `SIGNED_LEN_MIN`, `parse.mode_value` and the
    library's own empty-value set. The analyzer (`describe.status_burst_report`) reads the same root, with
    the H60A6's colour decode, so a 0x15 / 0x0d value short of its layout is a gap as the runtime rejects
    it, and reports where the read stopped.
  - The decode changes are app-aligned and H60A6 only, from Compose4BaseInfoSingleRead.u and h60a1
    Info4Detail.Q:
    - a tag-0 record is walked on, not taken as the end;
    - an empty or short 0x23 timer value fails the read;
    - a mode value too short for its sub-mode's layout fails the read, where it was zero-padded into
      made-up values (`15 01 0a` read as 2560 K, below the device minimum);
    - an empty mode value fails the read (the H60A6's own is read past since: see the entry above).
  - A differential over 52,068 random bursts × piece counts changed the folded H60A6 state in 4,395 cases.
    Each of them falls in one of those categories, and no burst that read whole both times folds
    differently, except via a tag-0 walk-on (the records after a tag 0 now apply).
  - `test_every_colour_profile_matches_the_mode_color_parse_devices_yaml_resolves` now also fails if a
    profiled SKU's devices.yaml `mode_color_parse_status` differs from its `mode_color_parse`.
  - Spec half: eedafe7 (a773057 rebased), which flags TLV 0x40 / 0xA3 only where the screen reads them.
    `_spec.profile()` passes its new `f_40` / `f_a3` selectors.
- **The segment-mask caps are read from the spec's `len_cap` (rule 4 b).** `build.BRIGHTNESS_MASK_CAP`
  is read off a built `op15_brightness` node, as `COLOR_MASK_CAP` is off `op15_color`. The per-segment
  brightness frame and the 0x0b RGB frame pass the `len_cap` of the node they build. The library no
  longer reads a mask out of zero bytes to learn a cap, and the private `_MASK_CAP_0B` is gone. The
  frames built are byte-identical: the 10,557 builder cases of the differential match, and the caps
  are unchanged at 7, 14 and 13.
- **The H5083 tests run against its firmware image (S7a, test-only).** `tests/firmware_sim.py` runs a
  `spec/firmware.yaml` image as a peripheral: its accept rules, answers, effects, pushes, events and
  advert, under the image's cipher. Each request is read through the `govee_ble_frame` root, and
  every frame and advert is built through the spec's classes (`wire._spec.emit` / `serialize`; an echo
  re-emits the request as read, and padding is `govee_common::echo`). `fake_ble.FirmwareGovee` puts it
  behind the bleak fake and honours the image's GATT layout: a write to another characteristic is lost,
  and it notifies only on its own. The bleak link and the transport failure knobs moved to
  `fake_ble.Peripheral`, which the generic `FakeGovee` also uses.

  `tests/test_h5083_firmware.py` drives a real `Device` for the H5083 (`h5083_sw1_00_30`) through these
  cases, checking what the library sent by request name, read through the spec:
  - the pairing window;
  - a wrong secret;
  - the relay write and read-back;
  - the once-per-device info reads (only `aa 06`, `aa 07 03` and `aa 07 02`);
  - a paired session the plug drops after 10-11 s idle, and its reconnect;
  - the library's own idle timers (on a virtual loop clock) against the plug's heartbeat: it closes a
    link 8 s after a poll, before the plug's 10-11 s drop, and after a 33 write its 30 s warm window
    outlasts that drop, so the plug ends the session (pinned as they are);
  - the advert.

  It replaces the H5083 tests that used hand-built frames:
  - `test_device_transport.test_plug_wrong_secret_is_auth_error`;
  - `test_device_v3.test_read_secret_bootstrap` (an H5080, the same goodsType 43 profile);
  - `test_identify.test_identify_encrypted_device`, and the parsed-pact lines of
    `test_identify_unparseable_pact_is_plaintext_unknown`;
  - the H5083 rows of `test_plug_power_frame_per_goods_type`, `test_plug_relay_bits_follow_the_detail_screen`
    and `test_device_info_reads`' captures. The H5080 rows still pin goodsType 43's frame bytes and relay
    bit, and the other SKUs' captures still pin the reply layouts.

  `test_fix_wp3_review.test_device_info_one_shot_is_used_up_by_a_responsive_plug_poll` is deleted.
  `test_device_info_survives_a_silent_poll_after_a_responsive_one` (H6008) covers it.
  `test_plug_advert_on_off_follows_the_app` keeps goodsType 43's advert mask on H5080 rows, because the
  H5083 only advertises 0 or 1. `test_scanner` takes its plug advert from the simulator.

  Two checks in `tests/test_firmware_provenance.py` keep it so:
  - At run time, an autouse fixture (`fake_ble.Provenance`) fails a test whenever an image-SKU device
    gets a frame or advert its image didn't build and send it:
    - A frame enters such a device's connection only as one delivery of a frame its image built, on the
      link it arrived on.
    - Every RX frame read while that device or its connection works must be such a delivery on the live
      link. That catches a FakeGovee's reply, a mocked `_conn.query`, a frame handed to `sim.send`, and a
      genuine frame replayed after a reconnect or read by another device.
    - Every Govee manufacturer payload read for the SKU must come from a live image of this test (or one
      made at import), whether it is read through the Device, `identify()` or the scanner.
  - Its scope:
    - The check also covers a Device built from an image SKU's profile without `sku=`, and Device
      subclasses.
    - It skips reads made to log or describe a frame, and other companies' manufacturer entries.
    - It runs on a private MonkeyPatch, so a test's `monkeypatch.undo()` keeps it, and a test that
      overrides its fixture fails.
    - It also fails a test that sends an image a request the image can't read.
  - Statically, the simulator (firmware_sim.py, `FirmwareGovee` and the fake_ble helpers it calls)
    builds no bytes outside the spec classes. Its one conversion is `_image_bytes`, for the image's own
    values. It uses no bytearray, `fromhex`, `struct`, `binascii`, `codecs` or padding, no int lists,
    no writes of ints, no concatenation, and no imports outside the spec side of the library.

  No change under `src/`.
- `CLAUDE.md` gains "The spec is the bible", the anti-debt rules for `spec/`:
  - fix the spec in place;
  - structure wins over library use;
  - one canonical path per message;
  - six labelled entry points;
  - context as params;
  - nothing dead;
  - cite the decompile, never copy it.

  A closing list maps rules 3-7 to the Suite S lints that check their structure; the rest is for review.
  `CONTRIBUTING.md` ground rules point to it, name the firmware as a source next to the app, give the
  `doc-ref` citation form, and list Suite S in the `scripts/check.sh` gates.
- The library moved onto the clean spec (spec/clean-spec a3e3e92: nine `spec/*.ksy` files, six of them
  roots, every frame a `govee_ble_frame(direction, profile)`), with no change to the bytes sent or the
  decoded state:
  - `wire/_spec.py` is the library's one import of the generated classes and the spec's read-write
    contract (read a parametric root, link and check every node, write in two passes with the checksum
    by `checksum_kind`); `_spec.profile()` builds the `govee_profile` (command forms, counts, colour-group
    rule, power rule, mode decode) of a set of device facts, cached, and
    `devices.profile.wire_profile()` maps a `DeviceProfile` onto it.
  - `wire.build` writes `write_command` / `mode_write` / `color_15_write` / `op15_color` /
    `read_request` / `status_request` / `a3_packet` / `a4_packet` trees; the masks are the spec's
    `seg_mask` over the posSet rounded up to whole bytes. `build.frame()` is gone (tests build raw frames
    with `fake_ble.frame`).
  - `wire.parse` reads a reply's `govee_state` under the profile it needs. The mode decoders collapse to
    `mode_reply(frame, prof)` (the 0xAC 0x05 TLV is read in the status stream) and `mode_color()` (a `KelvinReport`, the bars'
    `BarKelvin` or `(rgb, kelvin)`), which `_fold` folds whatever the SKU's layout; `parse_mode_0d*`,
    `parse_kelvin`, `parse_bar_kelvin`, `parse_active_scene` and the `*_of` decoders are gone. Plug power is
    `parse.parse_relay_on` (`power_state.is_on_heartbeat` with the profile's relay mask).
  - `wire.reassemble` reads chunks as `status_chunk`, TLVs as `status_tlv(status_profile(pieces))` and the
    colour groups by the spec's `color_group` rule (`group_record_count` is gone); `identify` reads
    `govee_manufacturer_data`, the handshake `handshake_request` / `handshake_reply`, scene uploads
    `govee_upload(kind, form)`.
  - Kept as library rules until the approval list decides them: the TLV walk's edges (a type-0 byte ends
    it; an empty switch / brightness / mode / device-info / zone / seg-info / colour-group value doesn't
    read), the padded TLV 0x05, the default `"rgb_kelvin"` parse's unsigned Kelvin, group 0 dropped with no
    pieces, the XOR drop, and a short `aa 05` reply dropped where its 0x15 / 0x0d / 0x13 layout doesn't
    fit.
  - Verified against cf03c90: 10,557 builder calls byte for byte (errors included) and 179,152 decode
    results (replies, mode folds per profile, status bursts, adverts, handshake, scene uploads). What
    differs: `describe` labels use the spec's enum names (`write/power`, `read/cmd_36`, ...) and every
    command counts as a known status TLV tag; the connection's known RX proTypes are the spec's RX routes
    (0x05, 0x3F, 0x66, 0xA5, 0xB0, 0xB3 and 0xB4 no longer log as unknown); and
    `parse.parse_device_info_selector` returns 3 for an `aa 07 03` reply whose text is not UTF-8 (was None;
    the read still yields nothing).
  - Build: `tools/gen_kaitai.sh` compiles every `spec/*.ksy` in one call (`--python-package .`), the build
    backend and the test staleness check list its nine modules, and `scripts/check.sh` runs the spec's own
    gate, `spec/checks/run.sh`.
  - Removed from the wire modules (none was in `govee_ble_local.__all__`, so this is not a public-API break,
    but code that imported them directly must move): `build.frame`; `parse.rx_frame`, the per-SKU mode
    decoders `parse_kelvin`, `parse_bar_kelvin`, `parse_active_scene`, `parse_mode_0d`,
    `parse_mode_0d_for`, `parse_mode_0d_rgb`, `parse_mode_0d_rgblight` and `parse_mode_rgb_kelvin_tint`,
    their `kelvin_of`, `kelvin_signed_of`, `bar_kelvin_of`, `gradual_kelvin_of`, `cct_0d_of`, `rgb_0d_of`,
    `rgb_kelvin_0d_of` and `rgb_kelvin_tint_of` helpers, and `SUBMODE4COLOR_SUB_MODES` (use
    `parse.mode_reply(frame, devices.profile.wire_profile(profile))` and `parse.mode_color`);
    `reassemble.group_record_count` (the spec's `color_group.num_records_wanted`) and the unused
    `TYPE_SLEEP`, `TYPE_WAKEUP`, `TYPE_TIMERS`, `TYPE_BAR_SWITCH` and `TYPE_IC_NUM`; and the private
    re-exports of the generated classes (`build._F`, `_node`, `_load`, `parse._parse_type`, `_parse`; use
    `wire._spec`). `parse.mode_reply` now takes the profile.
- Documented deviation: the connection keeps dropping an RX frame whose XOR checksum fails, although the app
  never checks it (`ComposeBleComm.K:190-211` drops only a frame that isn't 20 bytes), so a corrupted frame
  is not decoded as state.
- More of the library reads and writes through the spec's types (frame audit section F and the section-E
  additive forms):
  - every `aa` read waits for the reply its own request names (`parse.request_command`), not a hand-paired
    `build.CMD_*` key (12 sites);
  - read requests are the spec's `read_request_sized(selector_len)`;
  - segment masks are written by the spec's `segment_mask` (its width rule, LSB first), within the
    library's 7-byte cap;
  - the `mode_read` decoders use its `sub_mode` and `is_submode4color` instances and read the params with
    `mode_read_params`;
  - a direct `aa a5 <group>` reply is read by `color_group_direct_frame(record_count)`, and a 0xAC 0xA5
    group by `color_group_status_counted(pieces, 4, 1)`;
  - the TLV walk stepped by `status_tlv.total_len` (since replaced by the `govee_status_stream` root read,
    rule 4 c);
  - the live IC-count walk skips on the spec's `ic_count_checked` instead of a hand `>= 0x1000` test.
  Byte- and state-identical: equivalence run against c823fc5 (writers, readers, the TLV walk, parse_status,
  the mode decoders and folds, and every bench capture); the only differences are the new
  `IcCount.ic_count_checked` field and `_readback.live_ic_pieces` called directly with a count the walk
  never passes it.
- On the spec's section-E lockstep readers (2667e85): `build.secret_check` and `build.gradual` write the
  spec's `SecretKeyWrite` / `GradualWrite` in place, now that `single_command.params` types 33 B2 / A3 (same
  bytes); the 0xAC TLV walk reads 0x07 as the spec's `status_device_info` (`app_decodes` for the 0x10 / 0x11
  selectors the app's `w()` decodes) instead of stepping past the others itself. A 0x05 mode TLV that the
  spec's `mode_read` can't read (it types the value exactly; the app's per-sub-mode minimums aren't typed yet)
  is handed on raw, as before. Byte- and state-identical (equivalence run against 8c89c31, every bench
  capture identical). `govee-ble-analyze` no longer reports such 0x07 blocks, or 0xA5 groups whose len isn't
  1 + 4k, as spec gaps, now reports a 0x05 value `mode_read` can't read, and counts a TLV the walk hands on
  once in its TLV types (it counted a handed-on 0xA5 twice).
- The test coverage floor in `scripts/check.sh` is now 95% of statements and branches (`--cov-branch`;
  was 85% of lines), the same gate hass-govee-ble-local applies to the integration. Coverage today is
  98% with branches. Development tooling only.
- **The generated Kaitai classes are a build product, not committed source.** `src/govee_ble_local/_generated/`
  is gitignored. An in-tree PEP 517 backend (`_build_backend/govee_build_backend.py`, `[build-system]
  backend-path`) wraps setuptools and runs `tools/gen_kaitai.sh` before every sdist, wheel and editable
  build. The sdist and wheel ship the result, and a wheel built from an sdist (which carries no `spec/` or
  `tools/`) reuses the classes the sdist ships; with neither, the build fails with a message saying how to
  get them. Building from the repository (`pip install -e .`, `pip install .`, `python -m build`) therefore
  needs the JVM `kaitai-struct-compiler` 0.11; installing a released wheel or sdist doesn't, and the
  published wheel has the same files as before. A git or VCS URL (`govee-ble-local @ git+https://...@ref`)
  or a GitHub "Source code" archive is a repository build too, and needs bash and the JVM compiler, which
  Home Assistant doesn't have: to test an unreleased build in Home Assistant, pin the wheel attached to a
  prerelease GitHub Release, never a git ref. A `pip install --dry-run` of a git ref doesn't catch this,
  because preparing metadata skips the generation step. `scripts/check.sh`'s drift check and
  `gen_kaitai.sh --check` are gone; the readwrite build now clears `_generated/` first, so no leftover
  module can ship. CI and the release workflow run `scripts/smoke_artifacts.sh`: the sdist must carry the
  classes and no `spec/` or `tools/`, and the wheel, and a wheel built from the sdist, are installed into
  fresh venvs and must import and build a frame, so a wheel without the classes can't be published. `pytest`
  stops at once, saying to run the generator, when the classes are missing or older than `spec/*.ksy` or
  `tools/gen_kaitai.sh` (an ignored `_generated/` survives a `git switch` or `git pull`). A repository build
  that can't run the generator now ends with one message naming bash, the JVM compiler and the PyPI release,
  instead of a `CalledProcessError` traceback.
- **The `aa 05` mode decoders read the spec's `mode_read`, and the app's sign and CCT tests come from the
  spec.** `wire.parse.mode_reply` (an `aa 05` reply) and `wire.parse.mode_value` (the 0xAC 0x05 TLV) return
  the same `mode_read`, and the per-sub-mode decoders (`scene_of`, `kelvin_of`, `bar_kelvin_of`,
  `gradual_kelvin_of`, `cct_0d_of`, `rgb_kelvin_tint_of`, `rgb_0d_of`, `rgb_kelvin_0d_of`, `sub_mode`; all
  new) read it. The `parse_*` frame functions are unchanged wrappers. So the burst's mode no longer goes
  through an `aa 05` frame built by hand (`devices._fold.mode_reply_frame` is gone, and no library module
  calls the raw `wire.build.frame` helper now). The colour decoders no longer check the frame header or
  slice it by hand. The Kelvin sign tests use `mode_color_0d_typed`'s `kelvin_signed`,
  `report_kelvin_signed`, `is_cct` and `is_cct_positive` instead of `int.from_bytes(..., signed=True)` and
  `>= 0x8000`, for H6062, the bulbs and H6188, H6061 and H60A6. No decode changes: an equivalence run over
  7,360 20-byte replies through every decoder, 104,895 reply folds and 90,045 TLV folds (every profile, TLV
  values 0-20 bytes long) matched the previous code. Only frames shorter than 20 bytes, which the
  connection drops before decoding, decode differently. Bench 2026-10-16 (Mac, read-only): H6006, H6008,
  H6047, H6052, H60A6 and H61A8 polled cleanly with the new decoders. Today's bench captures (447 replies:
  55 `aa 05`, and 10 bursts whose 0x05 TLVs are 4 bytes long) decode the same through the old and the new
  code.
- **The library reads the spec owner's gap batch (c706502) instead of hand-kept values.**
  - **Enums:** `wire.build`'s `INFO_*`, `POWER_*`, `RELAY_*`, `CMD_TOTAL_SWITCH`, `COMM_*`, the op15 colour
    variants, the mode request selector, the 0xAC request command and the 0xA3 start marker now come from
    `device_info_selector`, `light_power`, `relay_write`, `plug_command`, `multi_comm_byte`,
    `op15_color_variant`, `mode_read_selector`, `multi_ac_command` and `a3_start_marker`. The
    `mode_color_0d_typed` kinds come from `mode_0d_kind`, `reassemble.TYPE_*` from `status_tlv_type`, and the
    connection's received-proType set from `rx_pro_type` (it now also knows 0x3A, 0xA6 and 0xAB, so those no
    longer log an "unrecognised frame" warning; they were queued all along). The analyzer's
    `KNOWN_STATUS_REPLY_TLVS` is the `status_tlv_type` enum, which adds 0x16, 0x39 and 0xA3.
  - **Types:** H5089's `33 02` main switch is written with `total_switch_write`, so no frame body is
    written raw any more. The `aa 41` section count is read with `section_count_read`. A 0xAC reply chunk is
    read as the frame's `status_chunk` (`reassemble.read_ac_chunk`, new), whose `len_last_fits` is the
    app's whole-burst reject, so the hand-kept chunk offsets are gone. A write ack is read with `rx_frame`'s
    `write_ack` (`wire.parse.parse_write_ack`, new), for the connection's rejection check and the analyzer.
    The BGC-info value is read with `bgc_info` (`wire.parse.bgc_encrypt_version`, new; lenient for a short
    format-2 value, as chosen), and the handshake key with `handshake_key_reply_data`. The analyzer routes
    on `rx_frame.pro_type` and judges a masked chunk from the spec chunk's data and checksum. The scene
    upload reads each blob's lead byte with `h60a6_scene_param`, `h6052_graffiti_param`,
    `compose_scene_param` and `scenes_graffiti_param`. An advertisement is read with
    `manufacturer_data_lenient`, so the 4-byte length gate is gone (item 41's additive route).
  - **Instances:** `mode_read.scene_code`, `bar_switch_read.left_on` / `right_on`, `gradual_read.is_on`,
    `secret_read_reply.is_valid`, `notify_wifi.is_connected`, `status_zone.zone_a_on` / `zone_b_on`, the
    device-info `uid_is_invalid` / `uid_has_leading_pair` / `wifi_mac_is_invalid`, `version3.text` /
    `is_zero`, and `manufacturer_data.is_govee_pact` / `has_switch_state` / `switch_byte`.
  - The H60A6 and H6061 0x0d report is read as kind 3 (`rgb_kelvin_0d`, `[r, g, b][kelvin]`, the five
    bytes the app reads) instead of kind 2.
  - Also from the 4017e19 frame audit:
    - the zone, bar and gradual writers' 1/0 states come from `light_power`;
    - `status_query` names its TLV types with `status_tlv_type` (it had borrowed the `command` enum);
    - the TLV walk reads the stream with `status_reply_safe` (the same TLVs; `[]` for an empty buffer)
      and ends on `status_tlv_type.padding`;
    - the analyzer's mode-reply exemption uses `mode_read_selector.request` and `mode_read.scene_code`.
    These are byte- and state-identical (equivalence run against f182c20, and the bench captures).
  - `devices.yaml`'s h5080 `by_goods_type` now pins the plug profiles' `relay_power`, `relay_on_mask`
    and `advert_on_mask` (a drift test, keyed by its `models`). A second drift test checks every colour
    profile's `mode_color_parse` against the strategy `devices.yaml` resolves for its goodsType (the family
    by the record's goodsType, by SKU only at 0; then `assigned_goods_type`; then `by_goods_type`,
    `by_model`, the family default, pact and firmware branches). The spec's `kelvin_pair_15` (H6047) is the
    library's `"kelvin_15"` on a bar-switch profile.
  - Still left for a lockstep spec change: the `mode_read` 0x04 switch case, the `command` enum's 0x02
    member, `pro_type` 0xA6, the root scan-record walk and the guard on `status_reply` itself.
  An equivalence run against the previous code found no other difference: the writers, every bundled scene's
  upload for every profile, 6,000 advertisements, the 0xAC bursts and TLV walks, the handshake replies, and
  today's bench captures (972 frames, 48 bursts, 230 encrypted replies) all decode the same. The decode
  changes are under Fixed.
- **More library sites read the spec's own fields instead of raw bytes** (an independent inventory of
  sites that ignored an existing spec field or instance).
  - `wire.parse.find_reply` matches a reply on the frame reader's `pro_type` and `command`. A reply
    whose body doesn't parse, such as an `aa 06` whose text isn't UTF-8, is still found.
  - `parse_direct_color_group` reads the reader's raw body, and `parse_section_count` matches its
    header via the reader (`on_off_memory`). Its body stays hand-decoded until the spec has a type.
  - `parse_active_scene` reads the code with `scene_payload`; `mode_read` has no 0x04 case yet.
  - The new `parse_device_info_selector` reads the `aa 07` selector for `_readback`.
  - `wire.build`: the 0x0b colour writer uses `mode_payload`'s own `color_rgbic_0b` type (a u2le mask).
    `status_query` names the requested types with the `command` enum.
  - The 0xAC TLV walk takes its cut reasons from `status_tlv`: `len`'s `valid` (max 0x7f) and how far
    the reader got. It drops its dead negative-length re-check after a whole `status_reply` parse.
    `TlvCut` gains `negative_len`, and the analyzer uses it. A skipped 0x07 block, and the device info
    `parse_status` folds, are read with `status_device_info`. A colour group with no piece count uses
    `color_group_status.records`.
  - The analyzer labels unknown values from the reader's fields.
  - The handshake reply is read by the frame reader.
  - The H60A6 scene upload picks DIY or graffiti with the spec's `h60a6_scene_value.is_diy`, and keeps
    the app's graffiti fallback for a DIY that doesn't parse (spec item 26).
  - The device's mode read hands the reader's `mode_read` straight to the fold.
  - A 0x15 colour write's mask cap is the spec's `op15_color_write.mask_cap`, which the writer already
    enforces: 7 bytes for variants 12 and 11, 10 for variant 5. No limit changes. The library builds
    variants 12 and 11 at that same 7-byte cap, and H6061's variant 5 with a fixed 3-byte mask.
    `MAX_MASK_BYTES` (56 segments) stays for the masks the spec has no cap for yet: op15 brightness and
    the device's segment count.
  No decode or frame changes, apart from the IC count under Fixed. An equivalence run over every
  20-byte case matched the previous code:
  - 2,435 frames through these readers and the analyzer;
  - the 0x0b and status-query writers;
  - 4,014 TLV walks and 5,000 status parses;
  - 1,800 handshake replies;
  - 3,088 H60A6 scene choices, including the 84 bundled scenes.
  Today's bench captures (810 frames, 40 bursts, 193 encrypted replies) also decode the same. Bench
  2026-10-16 (Mac, read-only): H6006, H6008, H6047, H6052, H60A6 and H61A8 polled cleanly with these
  changes, including the encrypted H60A6 and H6008 handshakes and the `aa 06` / `aa 07` reads. H6641,
  which uses the live IC count, was out of range.
- **Every frame the library sends is written by the spec's generated Kaitai classes, and every frame it
  reads is parsed by the same classes.** `govee_ble_local/_generated/` now holds Kaitai 0.11 read-write
  classes (`ReadWriteKaitaiStruct`), generated by `tools/gen_kaitai.sh`, whose default build is now
  read-write (`--read-only --out DIR` still writes auto-reading readers to a scratch directory).
  Each `wire.build` builder is a `GoveeBleFrame` tree of the spec's own types. Where the frame's read
  dispatch has no case for a write body or types it as the reply, the spec's standalone write type is used:
  `ZonePowerWrite`, `BarSwitchWrite`, `GradualWrite`, `SecretKeyWrite` and `Op15ColorGroupWrite` for the
  untyped bodies; `ReadRequest` for every `aa` request; `Op15ColorWrite`, `Op15BrightnessWide` and
  `ColorRgbic0bWide` for the colour bodies. The `e7` handshake frame is written the same way. Only what
  Kaitai can't express stays in code: the frame checksum, upload chunking and packet counts, the app's
  tint/Kelvin choices and the caller-input checks. Every frame is byte-identical
  (`tests/test_wire_build_vs_spec.py`, every builder against the app-traced catalogue). Cost: building a
  frame takes about 20-55 µs and parsing one about 10-15 µs (about 1 and 3 µs before).
- Code that uses `govee_ble_local._generated` directly (not public API): `from_bytes()` and
  `Cls(..., stream)` no longer read; call `_read()` on the result.
- `wire.build.plug_sync_time` refuses a time-zone hour or minute outside -128..127 (the spec's `s1` fields)
  with `GoveeBleInvalidValue`, instead of masking it to a byte. Real offsets are within -14..14 hours.
- **`aa 01` and `aa 04` are sent without the `01` selector byte**, as the app sends them
  (`ControllerSwitch.Companion.b`, `ControllerBrightness.Companion.b`, the plugs' `SwitchController` /
  `HeartGMessage`): `aa 01 00…` / `aa 04 00…`. No firmware image analysed reads byte 2 of these requests
  (it is overwritten with the state), so devices answer the same. **A light's `aa 01` reply now means on only
  for a byte of 1** (`ControllerSwitch.Companion.c`; the spec's `light_is_on`), as every curated control
  screen reads it; it was any non-zero byte. Firmware and the app only ever store 0 or 1, so no state reads
  differently in practice (the byte-2-or-more case is source-modelled). Plugs keep their relay-mask rule, and
  the H60A6's power still comes from its status burst, where any non-zero byte is on (`status_switch.is_on`,
  `Compose4BaseInfoSingleRead.u`). Live-checked on H6006, H6008, H6047 (on) and
  H6052, H61A8 (off).
- **Plug power is each goodsType's main switch** (Govee Home 7.7.02): the H5082 (goodsType 50) and the
  H5160 / H5161 (90) send `33 01 FF / F0`, which switches every outlet (`UiV2` / `UiV4.onOffChange` ->
  `SwitchControllerV2(on, 15)`), and the H5089 (307) sends `33 02 01 / 00` (`Ui4H5089.onOffChange` ->
  `Controller4TotalSwitch`). They all sent `33 01 11 / 10`, which switches outlet 0 only, so `set_power(False)`
  left the other outlets on and the next poll reported the plug on again. The H5080 / H5083 / H5085 (43) keep
  `33 01 11 / 10`. `set_power(True)` on a partly-on plug turns every outlet on. The `33 b5` time sync still
  follows. The H5082 and H5089 now have a profile each, so their `DeviceProfile.skus` is `("H5082",)` /
  `("H5089",)`. Not modelled: after ON the H5089 app re-applies its cached night-light mode. Source-modelled:
  no multi-outlet plug was in Bluetooth range (the H5083's frame is unchanged).
- **H6006 colour temperature is 2700-6500 K** (was 2000-9000): the H6006 is goodsType 64, which the app maps
  to `KelvinConfig` version 2 = [2700, 6500], as for the H6008. `set_color_temp` clamps to it, and an RGB pick
  equal to a tint colour outside it is sent as RGB, as the app does. Home Assistant's slider narrows to match.
- **The H60A6 poll is the 0xAC status burst alone**, the app's connect read: the mode comes from the burst's
  0x05 TLV and the `aa 05 01` read that followed is no longer sent (one frame fewer per poll). The mode is
  decoded as the H60A6's `Info4Detail.Q` does (`pact_h60a0` `h60a1.Info4Detail`): 0x15 `[gradual][kelvin
  s16be]`, 0x0d `[r, g, b][kelvin s16be]` with colour temperature only for a Kelvin above 0, else the RGB.
  Before, 0x0d took any non-zero Kelvin (read unsigned) as colour temperature. A 0x15 Kelvin of 0 or below
  still clears the Kelvin, as before; the app keeps its last one. Live-checked for a 0x15 report (2700 K)
  on the bench H60A6; the 0x0d branch is source-modelled.
- `tools/gen_kaitai.sh` generates Kaitai 0.11 read-write (serializer) classes into `_generated/` (see
  above). Its default build needs the JVM `kaitai-struct-compiler` 0.11.x on PATH
  (`brew install kaitai-struct-compiler`; `KSC=` overrides), because the npm build's `compile()` API has no
  read-write switch. `--read-only --out DIR` still writes auto-reading readers to a scratch directory and
  is the one mode that can fall back to the pure-JS compiler; it refuses `_generated/` as its output. Its
  `--check` mode is gone: nothing generated is committed to drift from (first entry in this section). Needed
  only to build from the repository (see that entry); released wheels and sdists don't need it. Its
  missing-compiler error now also names `bash scripts/install_ksc.sh` (Debian/Ubuntu) and the PyPI release,
  which ships the classes.
- Device info is read with `aa 06` (firmware version), `aa 07 03` (hardware version) and `aa 07 02`
  (device id), and nothing else: the `aa 07 11` Wi-Fi read and the `aa 07 10` basic fallback are no longer
  sent. No firmware image has a handler for those two (most drop the request, which cost a timeout on every
  poll; the H60A1 / H60A6 echo it back as zeros) and the app never sends them. The three that remain are
  handled by every firmware image analysed (H6006, H6008, H6047, H60A1, H60A6, H5083) and were answered
  live (2026-10-15) by the H60A6, H6047, H61A8, H6052, H6006, H6008 and H5083. The app sends `aa 06` /
  `aa 07 03` on the lights' control screens except the H60A6's (whose versions come from the 0xAC status
  TLV), not on the H5080 / H5083 / H5085's, and `aa 07 02` only while pairing; there they are kept as
  firmware-sourced extensions the app does not use (CLAUDE.md rule 6), marked as such in the code with the
  firmware handlers they rely on. Before these reads, only the H60A6 reported a
  firmware version, because the others ignore `aa 07 10`. `wifi_mac` now comes only from the H60A6's
  status TLV (the `aa 07 11` read never returned one).
- **The "device rejected write" log line moved from ERROR to DEBUG**, and the write raises the public
  `GoveeBleCommandRejected` instead of a private class, so the caller decides what to log. An integration
  that relied on the ERROR line, for example an always-on WARNING capture for diagnostics, must log
  `GoveeBleCommandRejected` (and `GoveeBleAuthError`, a rejected secret) itself, to a logger its capture
  covers (`docs/DIAGNOSTICS.md`). Until `hass-govee-ble-local` does, rejections are missing from its
  downloadable diagnostics.
- An RX frame with an unknown proType is logged at WARNING once per proType for the device's lifetime
  (until the integration reloads); repeats go to DEBUG and cost nothing when DEBUG is off, so a device that
  keeps pushing one can't flood the log.
- Handshake failures raise `GoveeBleHandshakeTimeout` / `GoveeBleHandshakeConnectionError`, which subclass
  the `GoveeBleTimeout` / `GoveeBleConnectionError` raised before, so `except GoveeBleHandshakeError` now
  fires. That includes a failed `e7` write (BleakError "Not connected", `BrokenPipeError`, `EOFError`, or
  bleak's characteristic-not-found error from a stale GATT cache), which 1.3.0 raised as
  `GoveeBleConnectionError("connect to <address> failed: ...")` (a `BrokenPipeError` escaped unwrapped); its
  message now reads `<address>: handshake write failed: ...`.
- A connection raises only `GoveeBleError` subclasses: an unexpected backend error while connecting or during
  an operation (e.g. a `RuntimeError` from `start_notify`), and a `BrokenPipeError` / `ConnectionResetError`
  from the BLE stack, become `GoveeBleConnectionError`, chained to the original, and the link is closed.
  Once a device is known to use AES-GCM (BGC v2), later calls raise `GoveeBleNotSupported` without opening
  a link each time. `GoveeConnection.send_many()` rejects a non-write frame with `GoveeBleInvalidValue`
  before any I/O. Not hardware-verified.
- Connection error messages changed; anyone matching on the text needs the new forms. `connect to <address>
  failed: ...` is now `<address>: connect failed: ...`, and `<address>: connect/handshake timed out` is now
  `<address>: connect timed out`. A write timeout raised by the BLE stack reads `<address>: BLE I/O timed out
  (...)`. An unexpected backend error reads `<address>: unexpected transport error: <repr>` during an
  operation and `<address>: connect failed: <repr>` while connecting.
- `discover()` raises `GoveeBleConnectionError` when the scan itself fails (no adapter, adapter powered
  off, BlueZ / D-Bus unavailable). That error is also the `BleakError`, `OSError` or `EOFError` 1.3.0 let
  through, so existing `except BleakError` handlers keep matching. A device reporting 0 dBm now sorts as
  the strongest, not with the no-RSSI ones.
- Typed argument errors: a request a colour scheme can't express (per-segment colour on a whole-device
  scheme, CCT on H61A8) raises `GoveeBleNotSupported` from the builders instead of a bare `ValueError`
  (not a `ValueError`, so `except ValueError` around a direct builder call no longer catches it), and
  `Device` re-raises it with the SKU on every path. Bad input (a non-integer brightness, an 0xA4 `mtu`
  outside 20..512 or an upload over its 16-bit packet count; at the `Device`, a malformed RGB, a non-int
  brightness, a `str` or other non-bytes secret) raises `GoveeBleInvalidValue` before any I/O, and
  `turn_on` now checks every argument before the segment-count read. `Device(secret=...)`,
  `create_device(secret=...)` and `set_secret()` take `bytes`, `bytearray` or `memoryview` and store
  `bytes`. An unknown zone or scene name raises `GoveeBleUnknownName`, still a `GoveeBleNotSupported`, so
  existing handlers keep matching.
- `device_profile_for` is deprecated in favour of `profile_for` (the same lookup): it emits a
  `DeprecationWarning` and is removed in 2.0. `hass-govee-ble-local`'s config flow still calls it.
- `DeviceProfile` validates its fields at construction and raises `GoveeBleInvalidValue` (a `ValueError`)
  for empty or non-string SKUs, `brightness_max < 1`, a `brightness_range` whose 100 % overflows a byte,
  `color_readback_per_batch < 1` and a malformed mask, IC range or IC divisor. A list of SKUs is still
  accepted, and so is a custom profile pairing a capability with a colour scheme that can't express it, as
  in 1.3.0; its setter now raises `GoveeBleNotSupported`. The built-in table is checked at import for those
  pairings, a `mechanism_a_direct` group size other than 4, `color_readback_live_ic` without
  `color_readback_ic_range` and a duplicate SKU. The new fields come after every 1.3.0 field, so the
  positional signature and `dataclasses.fields()` order are unchanged.
- `DeviceProfile.brightness_max` is deprecated (documented only, no warning); use `brightness_range`.
- **H6052 colour read-back is RGB only.** After a poll, a lamp in colour-temperature mode reports the report's
  colour as `rgb_color` (for a CCT the app or this library set, the Kelvin's tint) and no `color_temp_kelvin`,
  as the app's control screen reads it: a `0x0d` report is `[r, g, b]`, its Kelvin bytes never read, and
  `0x15` and the other colour sub-modes are ignored (`H6052NewDetailVM` -> `H6052InfoDetail.G`). `aa 05 0d ff
  ff ff 0a 8c` now gives RGB (255, 255, 255) instead of 2700 K. Modeled from the decompiled app. Live-verified
  on an H6052 (2026-10-15, macOS): after a 2700 K write the lamp reports `aa 05 0d ff ae 54 0a 8c ff ae 54`,
  which now reads as RGB (255, 174, 84) with no Kelvin; what the app displays for that report is
  source-modeled.
- 0xAC status read-back: a reply that would fail the app's read gives no state, as the app shows none
  (`Compose4BaseInfoSingleRead.u` throws, `AbsComposeControllerWithOpResult.onOvertime` fails the read, and
  the `VM4LightH60A6` success callback that applies state never runs). That covers a TLV the spec can't read,
  a TLV length byte of 0x80 or more (the app reads it signed) and a complete burst whose last TLV runs past
  the reply. `Device` retries once, then leaves state stale. A burst that lost a chunk or its last packet gives
  no state either (see the burst-assembly entry). Each poll that gets such a reply logs the existing `status
  read returned no usable data after retry` WARNING. Modeled from the decompiled app; not hardware-verified.
- `DeviceState.optimistic` on status-readback devices (H60A6) goes False only once a live field (power,
  brightness, zones, segments or the mode report) comes back. 1.3.0 cleared it after any 0xAC poll that
  wasn't discarded, even an empty or failed one; a poll that returned nothing, or only device info, now
  leaves it True. The one-time INFO `first poll` line also waits for the first poll that read something
  back.
- `MANUFACTURER_IDS` also lists 0x8841 and 0x8842, the encrypted v1/v2 broadcast headers the app accepts
  (`BleUtil.parseBleBroadcastPact`), so a Home Assistant manifest built from it matches those devices too.
  Modeled from the decompiled app; not hardware-verified.
- Signatures: `wire.build.status_query` takes `full` keyword-only; `handshake.build_step1` / `build_step2`
  drop their unused `payload` (the app never sends one); `Device.set_segment_*` accept any sequence of
  indices; whole-device colour builders called without a segment count use the scheme's default (23 panels
  for H6061, 15 for H61A8, not H60A6's 13). Removed: the superseded,
  unused `wire.parse.parse_mode_color_0d` (it dropped a 0x0d report's Kelvin), the unused
  `const.COMMAND_ACK_TIMEOUT`, and the internal `GoveeConnection(on_notify=...)` hook with its type alias
  `transport.connection.NotifyCallback`. `Device`'s frames are unchanged (it always passes its count); a
  direct builder call that omits `segments` now gets the scheme's default mask. Modeled from the decompiled
  app; not hardware-verified.
- Cloud account: a token-only `GoveeCloudAccount` (no email/password) whose token expires raises
  `GoveeCloudAuthError` instead of sending a login with empty credentials. Requests no longer follow HTTP
  redirects, so the password is never re-sent to a redirect target; a 3xx is reported as `login failed
  (HTTP 3xx)` / `<path> failed (HTTP 3xx)`. `CloudDevice.ble_mac` is documented as best effort.
- `govee-ble-analyze`: `-o` creates the converted JSONL with mode 0600 and never writes through a symlink
  (a failed write is `cannot write`, exit 2); `--help` keeps the module docs and documents exit codes
  0/1/2; `--from-frames-log` records carry the device address (`addr`). The "malformed 0xAC bursts
  skipped" count (and `analyze_status_bursts`' third element) counts only bursts that were not walked; the
  report now also counts complete bursts walked up to a TLV overrun (the runtime uses no state from them)
  and bursts from a redacted capture.
- Docs: README lists `profile_for` (`device_profile_for` as its deprecated alias), `create_device`'s
  `idle_disconnect` and `discover()`'s error. `docs/DIAGNOSTICS.md`'s log-level table matches the code
  (nothing at ERROR, every warning site listed, timeouts as `GoveeBleTimeout`) and its redaction note says
  what is masked (the account secret) and what is not.
- Encrypted sessions do the cipher key setup once per session (`crypto.FrameCipher`; identical bytes, less
  event-loop time). Log records keep the `govee_ble_local.devices.device` logger. Live-verified on encrypted
  H60A6, H6008 and H5083 sessions (2026-10-15, macOS): every session frame matches the per-frame cipher byte
  for byte.
- CI and the release workflow generate the Kaitai read-write classes from `spec/*.ksy` in every job
  that tests or builds. `scripts/install_ksc.sh` installs the pinned JVM `kaitai-struct-compiler` 0.11
  (the upstream `.deb`, checked against its published SHA-256, and refused unless it matches
  `tools/gen_kaitai.sh`'s pin), and `scripts/check.sh` now runs `tools/gen_kaitai.sh` before every other
  gate, so a local run needs that compiler too (`brew install kaitai-struct-compiler`). Development
  tooling only.

### Fixed
- **The suite passes at the dependency floor again (tests and tools only).** CI's lowest-direct job (bleak 0.21,
  bleak-retry-connector 3.0.0, cryptography 41.0.0, kaitaistruct 0.11) failed collection on a test that imported
  `BleakCharacteristicNotFoundError`, which bleak 0.21 lacks. Three more places built a `BLEDevice` without the
  `rssi` that bleak < 1.0 requires: two tests and `tools/fetch_scenes.py`. They now use the version-aware
  `fake_ble.ble_device` (the tool uses the same check inline), and the test falls back to a `BleakError` with the
  same text. The library itself builds no `BLEDevice`, so it was unaffected.
- **A flaky timing assertion in the H5083 idle-link test (test-only).** The virtual clock adds the float timeouts
  it jumps, so 9.0 + 3.0 s can land a hair under 12.0 (CI saw 11.99999999999998). The check now allows 1e-6 s.
- **The spec conformance tests run again (test-only).** `tests/test_spec_conformance.py` still imported the
  advertisement reader that S1 deleted with `govee_adv.ksy`. Under `CI=true`, which CI sets, that import
  stopped the whole pytest run at collection; locally the module was silently skipped. The devices.yaml
  schema and catalogue checks run again, and the advert check now reads its synthetic Govee AD through
  the labelled `govee_scan_record` root (its manufacturer-specific case is `govee_manufacturer_data`).
- **A 0xAC status chunk is a 20-byte 0xAC frame, as in the app.** `reassemble.reassemble` and `parse_status`
  took any record of 19 bytes or more in 1.3.0, whatever its proType. They, and the new `burst_chunks`,
  `BurstAssembler` and `describe.status_burst_report`, now take only a 20-byte 0xAC frame as a chunk, as
  `ComposeBleComm.K:198` (any other length is dropped) and
  `AbsController4BleSingleSendMultiBack.isSameController:207` do.
  - The connection only delivers 20-byte frames, so nothing changes at runtime. It affects callers that
    hand the reassembler records directly.
  - A chunk is read through the frame root (`govee_ble_frame(rx)`, one private helper), and
    `read_ac_chunk` raises `ValueError` for a record that isn't one.
- `spec/checks/lint.py` passes `mypy --strict` and pyright again. The root-label loops no longer reuse the
  type-path variable, and the reachability walk declares its path types.
- pyright is clean on `src/`, `_build_backend/` and `tools/` after the PORT. The TLV-walk test's
  helper reads a handed-on TLV's value bytes directly, so the PORT adds no pyright finding in `tests/`.
- **Segment masks follow the app's caps, up to 112 segments.** Each body carries at most its own mask bytes,
  and the app's writers drop the positions past them (`coerceAtMost`): the 0x15 RGB and colour-temperature
  frames 7 bytes (the spec's `op15_color_write.mask_cap` for variants 12 / 11: `Bytes.h:324`, `Bytes.r:499`),
  per-segment brightness 14 (`op15_brightness_wide.mask_cap`: `Bytes.G:179`). Before, the library refused
  every write above 56 segments. `build.MAX_MASK_SEGMENTS` is now 112 (`MAX_MASK_BYTES` 14), so a device
  or section count up to 112 is accepted; a colour write on more than 56 segments reaches the first 56, as
  in the app, and the segment state folds only the positions a frame carried. New:
  `build.COLOR_MASK_CAP`, `build.BRIGHTNESS_MASK_CAP`, `build.mask_positions(segments, mask_cap)`, and
  `build.mask_width(segments, mask_cap=14)`. No curated SKU has more than 16 segments, so their frames
  are unchanged. Source-modelled.
- **A successful 0xAC status read applies the app's switch and brightness defaults.** Each read starts from
  a fresh `DeviceBaseInfoFromDevice` (switch false, brightness 100; `DeviceBaseInfoFromDevice.java:70-79`),
  and the H60A6 success callback applies it whole (`h60a1.Info4Detail.n0:641-677`: `g(info.j())`, the
  switch; `i = info.b()`, the brightness). So a successful read whose stream has no 0x01 TLV now reads power
  off, and no 0x04 TLV brightness 100; before, those stayed as they were. A failed read still applies
  nothing. Captured bursts always carry both. Source-modelled.
- **gt 50 / 307 plugs (H5082, H5089) read power from relays 0 and 1**, the app's `aa 01` heartbeat rule
  (`HeartControllerV2` -> `BleOpV2.onEventHeartV2:216-231` / `BleOp4H5089.onEventHeartV2:331-341`, `open =
  i(0) | i(1)`; the spec's `switch_read_reply.any_relay_0_1_is_on`), which the library's repeated poll
  follows: `relay_on_mask` 0x03. Before, any relay bit (0xFF), the connect-read rule. Source-modelled.
- **Library scenes upload only what the app's validators accept** (the spec's section-E types). A type-1 RGB
  scene uploads only when `ScenesRgb.isValidProtocolBytes` accepts its blob (`ScenesOp.l:377-392`; the spec's
  `rgb_scene_value.is_valid`), a type-2 RGBIC one only when `ScenesRgbIC.isValidProtocolBytes` does
  (`ScenesOp.m:394-409`; `rgbic_scene_value`, which reads exactly that), and a type-3 graffiti one only when
  `ShareDiy.isValidGraffitiBytes` does (`ScenesOp.n:411-429`; `scenes_graffiti_check.is_valid`: size >= 9,
  lead 0x01, the group-header walk). Any other blob is only activated, as in the app; before, types 1 and 2
  went out unchecked and type 3 checked only the lead byte. Every bundled scene plans as before (631
  uploads, 25 activate-only, every profile); only a cloud or custom blob the app would not upload changes.
  Source-modelled; not hardware-verified.
- **0xAC status bursts are assembled as the app assembles them** (`AbsController4BleSingleSendMultiBack`
  as `ControllerSingleRead` runs it: `isSameController:202-225`, `onResult:273-308`). The first chunk must
  carry the request's command 3 and sub 0 (`f():100-103`); then each index is taken only in order, and the
  last packet only once every packet before it is in. A duplicate, an early or out-of-order chunk, an early
  0xFF or a first chunk of another command is ignored, as the app ignores it; a lastLen that doesn't fit
  fails the read at once. Only a complete burst is decoded: one that lost a chunk or its last packet now
  gives no state (one retry, then the state is left stale), where the library used to decode the prefix
  that arrived (the app applies nothing: `l()` never runs and the read times out). `query` ends on a
  complete or failed burst, ignores `terminal` for 0xAC, and only a chunk the app takes arms the idle gap,
  so a stray early 0xFF or a late chunk of an earlier burst no longer ends the read before the burst
  arrives. `burst_chunks` keeps its signature. Every captured H60A6 burst (16 reads) decodes as before.
  Modeled from the decompiled app; the failure paths are not hardware-verified.
- **The 0xAC TLV walk follows `Compose4BaseInfoSingleRead.u` at its edges.**
  - The buffer ending before a TLV's value starts (no len byte, or none after it, whatever the len) is a
    clean stop that keeps what was read (`u():257-267, :271-282`); before, it gave no state.
  - A 0xA5 colour group whose len isn't 1 + 4k is read with the app's record count, extra bytes ignored
    (VM `.o:112-116` -> `Controller4ColorInfoByGroup.Companion.q`); before, it stopped the walk.
  - With the piece count, a group the app can't place fails the read (group 0, or one starting past the
    last piece: `SubMode4Color.j:113-147` throws), and one starting exactly at the end is skipped; before,
    group 0 was dropped and a group past the end added phantom segments.
  - The last 0x07 device-info block wins (`w():370-437`): a basic block sets the serial when its UID is
    valid and always sets the versions (None when zero); a Wi-Fi block always sets the Wi-Fi MAC. Before,
    the first block won.
  - Not changed, waiting for spec types: a type-0 byte still ends the walk (u() reads it as an ordinary
    TLV), and a short 0x05 mode value is still padded.
  Captured bursts decode as before. Modeled from the decompiled app; not hardware-verified.
- **H61A8 segment colours are placed by the batch requested**, as goodsType 19's screen places them
  (rgbiclight `VM4Light.N5:946-962` -> `Controller4ColorInfoByGroup.Companion.h` / `i`, which drop the
  reply's group byte, -> `P5` with the loop's group -> `SubMode4Color.j`). Before, the reply's own batch
  byte placed them and a byte outside the walk dropped the batch (the legacy dreamcolorlightv1 `BleOpV3`
  screen, which goodsType 19 never opens). Bench replies carry the requested batch, so they read as before.
- **A raw brightness byte reads as the control screen shows it** (`DelegateHeader.i0:230-239` ->
  `ViewBrightness.b:353-365`): on a raw range (H6188's 20..254) the byte is clamped to the range before
  `calculatePercent`, so it reads 1..100 (before: 0 for a byte below 18); on the percent range [1, 100] the
  byte itself is shown. `SameModelCmdOp.E1` agrees on a raw range. Source-modelled (no H6188 on the bench).
- **The plugs send the `33 b5` time sync as their app does.** Every plug screen sends it in its connect
  read and waits for its ack (`SyncTimeController`, an `AbsOnlyWriteSingleController`): gt 43
  `H5083NewDetailRepository.necessaryMessages:323-339`, gt 50 / 90 `BleOpV2.o0:141-171` /
  `BleOpV4.l0:124-147`, gt 307 `BleOp4H5089.V:145-163`. The library now sends it once per connection,
  acked, right after the `33 b2` unlock (`Device._session_frames`); a refusal or no ack is only logged.
  Before, it went out unacked after every power write and never on connect. After each acked power
  write, gt 50 (H5082) and gt 90 (H5160 / H5161) re-run their connect read list
  (`BleOpV2.onEventSwitchV2:380-382` / `BleOpV4.onEventSwitchV2:299-301` -> `G()`), so those plugs
  (`power_resync`) re-read `aa 06` and `aa 07 03` and re-send `33 b5`; their per-outlet timer and delay
  reads are not modelled. gt 43 and gt 307 send nothing after a power write. Differences kept: on gt 50
  / 90 the library reads `aa 06` / `aa 07 03` once per device rather than before `33 b5` on every
  connect. Live-verified on an H5083 (gt 43, 2026-10-17, macOS): it acks `33 b5` (status 0) about 60 ms
  after the unlock, a power write is followed by nothing more, and the next session sends `33 b2` then
  `33 b5` again. gt 50 / 90 / 307 are source-modelled.
- **Per-segment brightness is 1..100, the app's slider range** (`ViewColorMode.t0:232-235`,
  `DelegateColorV2Part.t0:459-462`: `setProgressRange({1, 100})`). `Device.set_segment_brightness` and
  `build.segment_brightness` clamp 0 and below to 1 (before: 0 was sent), as `set_brightness` already did
  for the whole device. Affects the 0x15-scheme segment devices (H60A6, H6047, H6641, H6062).
- **The H60A6 status read asks for the 0xA5 colour groups only on the pacts the app does**: its
  request is `[0x41, 0x30]` plus 0xA5 if `J5()`
  (`VM4LightH60A6$afterConnectedSingleReadDeviceInfo$1.invokeSuspend:158-161`; `VM4LightH60A6.J5:691-697`
  -> `Support.supportPartColor4H60A6:434-436`: goodsType 303, pact type 1, pact code >= 2). An H60A6
  advertising pact (1, 1) now gets `ac 03 02 41 30`; (1, >= 2) and a device whose pact hasn't been heard
  yet keep `ac 03 03 41 30 a5`. A reply without 0xA5 leaves the segments as they were.
- **The H60A6 graffiti (0xA4) upload is sized to the link's MTU**, as the app sizes it
  (`H60A6GraffitiParse.makeBleController:240` -> `Compose4DefWrite4Multi.d:233-246` with
  `MtuConfig.getAvailableMtuSize` = ATT MTU - 3, at least 20). The frames are planned once the link is up
  (`send_unacked` with a function of `write_payload_size`), so a reconnect re-sizes them. A link that
  reports no larger size (a BLE proxy, BlueZ before 5.62) keeps today's 20-byte frames. The 0xA3 DIY
  upload is unchanged.
- **Kelvin, version text and section count are read as the app reads them** (the spec owner's gap
  batch). All of these are app-matching and source-modelled: they change only values the bench devices
  don't report.
  - H6641's 0x15 Kelvin is read signed (a plain `SubMode4Color`, `BleUtil.getSignedShort`): 0x8000 or
    more is no colour temperature.
  - H6047's two bar Kelvins are read signed (`cct_read_reply.kelvin_signed` / `kelvin_right_signed`;
    h6047 `Info4Detail` D0/E0 `getSignedShort`). A negative bar Kelvin is treated as "not in colour
    temperature", like 0.
  - H61A8's colour reports are read as its screen reads them, with `mode_color_parse` `"kelvin"` (as
    H6062 and fd4f794's `devices.yaml`). goodsType 19 opens the rgbiclight `AcNewDetail`
    (dreamcolorlightv1 `SubLib4BleIot` -> `Model4BleIot.t:41-46`), whose base
    `OpInfo4Detail.parseModeValidBytes:766-778` sends 0x15, 0x0b, 0x02 and 0x0d alike to `SubMode4Color`:
    `[gradual][kelvin s16be]`, colour temperature only for a Kelvin > 0. Before, only 0x15 gave a Kelvin, read
    unsigned (dreamcolorlightv1 `SubModeColorV2.parse`), which is the legacy AdjustAc screen goodsType 19
    never opens. Modeled from the decompiled app; not hardware-verified (the bench H61A8 rejects the
    library's colour write, so it sent no colour report).
  - Device-info version text is the spec's `version3.text`, each byte read signed
    (`Compose4BaseInfoSingleRead.v:363-368`, as chosen), so a byte of 0x80 or more renders negative.
  - The `aa 41` section count's `sectionNum` is a signed byte (`section_count_read`). The device already
    refused a count of 0x80 or more, so its segment count is unchanged.
  - A graffiti (sceneType 3) library scene is uploaded only when its blob leads with 0x01
    (`scenes_graffiti_param`; `ScenesOp.n` -> `ShareDiy.isValidGraffitiBytes`); otherwise it is only
    activated, as in the app. All 25 bundled graffiti scenes pass. The app's other two checks (size >= 9
    and a walk of the group headers) have no spec type yet and aren't made.
- **`wire.parse.parse_ic_count` reads the IC count signed, as the app does.** It uses the spec's
  `ic_segment_read.ic_count_signed`, after `ControllerOnlyReadIcSegmentNum` (`BleUtil.getSignedShort`).
  An `aa 40` count of 0x8000 or more is now negative, as in the app. Before, it was returned unsigned,
  and only `_readback.live_ic_pieces` sign-extended it. Live-IC colour read-back (H6641) is unchanged,
  because `live_ic_pieces` already read it signed. App-matching and source-modelled: not
  hardware-verified for counts of 0x8000 or more, which no bench device reports.
- `govee-ble-analyze` knows the 0xAC status TLVs 0x36 (bar switch; the H6038 / H6048 status read requests
  it, `VM4H6038`) and 0x40 (IC count; H6671 / H6672, `VM4LightH667172`), so their bursts are no longer reported
  as spec gaps with exit code 1. New constants `wire.reassemble.TYPE_BAR_SWITCH` and `TYPE_IC_NUM`.
- Segment setters refuse an index at or past the device's segment count (H60A6 13, H6047 10, H61A8 15),
  with `GoveeBleInvalidValue` as for any bad index. The app's position set has exactly that many positions
  and its mask encoders can't set a bit past it (`BleUtils.Y` / `BleUtils.X`); the library took any index up
  to 15 on a device of 16 segments or fewer, so `set_segment_brightness([13, 14, 15], ...)` on an H60A6 sent
  the mask `e0 00`, which the app never sends. Frames for valid indices are unchanged. (H6641 still uses its
  static 16 until its count is read live.)
- A custom `DeviceProfile` with `brightness_range=(1, 100)` writes the percent as it is
  (`BrightnessConfig.Companion.c`); it wrote one more for 51..100. No table profile uses that range.
- `wire.parse.parse_section_count` reads the H6062's IC count signed (`BleUtil.getSignedShort`), as its
  docstring said. Only diagnostics read it.
- 0xAC status burst: the last packet is the first of tag 0xFF and tag `packets - 1` to arrive once the
  others are in, as the app takes it (`AbsController4BleSingleSendMultiBack.isSameController`). The library
  always preferred 0xFF, so a corrupted burst carrying both spliced the wrong chunk in (a phantom segment).
  The H60A6 sends only 0xFF, so its bursts decode as before.
- `GoveeConnection.query` ends a 0xAC burst as soon as it is complete by the app's rule
  (`wire.reassemble.BurstAssembler`): a one-packet reply, or a last packet tagged with its position, no longer
  waits for the 0.6 s idle gap. The same frames come back.
- RX frames of proType 0xA2 (`multi_read`, device to app in the spec) are no longer logged at WARNING as
  unknown.
- `govee-ble-analyze`: each 0xAC chunk's length and BCC are checked like any frame's (an artifact, and the
  chunk is left out of burst reassembly, as the runtime drops it; a chunk zeroed by the capture masking is
  not), and the TX status request is read with the spec reader (label `status-request/<N>`).
- H60A6 scene upload plan (type 5): as the app's new-DIY path (`BaseSceneViewMode.r3`, parser order
  `H60A6DiyConfig`), the decoded blob must start with 0x50, it goes out as a DIY (0xA3) only when the spec's
  `diy_value` reads it with its length prefix covering the rest, else as a graffiti (0xA4) when
  `graffiti_value` reads it, else the scene is only activated. Before, a blob of any first byte was uploaded,
  by the length check alone. All 748 bundled scenes plan the same frames as before. The app re-serialises
  what it parsed (`IDiyParse.toBytes`); that is not modelled.
- H60A6 status read: each 0xA5 colour group of the 0xAC reply is now read with the app's record count for
  the device's 13 colour pieces: 4 per group and 1 in the last
  (`VM4LightH60A6$afterConnectedSingleReadDeviceInfo$1.o`; `pact_h60a0` `Support.getColorPieceSize` /
  `getOneGroupColorSize`), read by `Controller4ColorInfoByGroup.Companion.q`. Extra records in the last group
  no longer add segments 13-15, and a group short of its count fails the read, as it does in the app (no
  state is used from that burst). Before, the count came from each TLV's length. Source-modelled; the bench
  bursts carry the app's counts, so they decode as before.
- macOS: the connect failed on the H60A6 and H6008 (`Failed to discover descriptors for characteristic
  13 ... UUID not allowed`) because CoreBluetooth rejects descriptor discovery on a characteristic
  of the Generic Attribute service. The connection now resolves only the Govee service
  (`services=[SERVICE_UUID]`; bleak's BlueZ and WinRT backends apply the same filter). Live-verified on
  four H60A6s and two H6008s from a Mac.
- The device id of an H60A6 was reported as `"00"`: its `aa 07 10` UID is all zeros, which the parser
  rendered as a value and so skipped the `aa 07 02` fallback. An all-zero UID is now no id
  (`NumberUtil.isInvalidUuid`) and a single leading `00:00:` is dropped (`SnController`). Live-verified on
  four H60A6s.
- `create_device` ignored `DiscoveredDevice.advertisement` (the parsed `GoveeAdvertisement` that
  `discover()` returns): only bleak's raw advertisement set the encryption mode, so the natural
  `discover()` -> `create_device(..., d.advertisement)` flow fell back to the SKU profile. For an H6008
  (profile: plaintext; real firmware: encrypted, pact 1/1) every read was then sent in plaintext and
  went unanswered. Both advertisement types are accepted now. Live-verified on two H6008s.
- Transport: `BrokenPipeError`, `ConnectionResetError`, `EOFError` and a write's `TimeoutError` get the same
  reconnect-and-retry as `BleakError`; a write timeout is no longer reported as "operation exceeded 45s";
  `query()` keeps the frames already received when its budget runs out (a complete burst came back truncated
  under event-loop lag); a single-reply read waits its full 2 s (a stray late reply cut it to the 0.6 s burst
  gap); and messages name the type of a message-less error. Not hardware-verified.
- The BGC-info characteristic is found the way the app does (`BgcInfoReader.a`): the first service with UUID
  1910, then its 2b12 characteristic. A firmware that also exposes 2b12 under the OTA service 1912 (on
  backends that don't filter services), or two 1910 services that each carry 2b12, made bleak's
  collection-wide lookup raise and every connect fail. Modeled from the decompiled app; not
  hardware-verified.
- A secret set with `set_secret()` on a live session is sent (`33 b2`) before the first frame of any setter,
  scene upload or read-back on it, as the app's pairing flow does (`AbsPairAc4SecretV1.onEventSecretKey`).
  In 1.3.0 a secret set on an open session was sent only at the next session setup, so the commands on that
  link went out without it. It is not lost if that write is cancelled or fails while the session stays up; a
  finished write (acked, rejected or unacked) is not resent. An unchanged secret, or one set on a profile
  without `requires_secret`, queues no `33 b2`. Modeled from the decompiled app; not hardware-verified.
- Scene uploads: a short value (14 bytes or fewer, including 15 bundled H6004/H6006/H6008 scenes such as H6006
  "Asleep") is sent as a START frame (packet count 2) plus a separate empty END frame
  (`MultipleControllerCommV1.makeSendBytesV2`), not one 0xFF frame with no START. A compose scene (sceneType
  6) is uploaded only when its blob starts with 0x0a (`ScenesOp.o`); others, H60B0-style 0x51 blobs included,
  are activate-only (no current profile reaches this). Modeled from the decompiled app. Live-verified on an
  H6006 (2026-10-15, macOS): "Asleep" went out as `a3 00 01 02 01 83 01 ff e1 96 ...` then `a3 ff 00 ...`, the
  device acked the upload and the activation, and the next read reported scene 1134. The compose gate is not
  hardware-verified (no profile reaches it).
- Colour frames the app never sends are refused: per-segment colour and brightness on the 0x0d bulb schemes
  (`h6006`, `h6188`) raise instead of building a 0x15 RGBIC frame, an unknown colour scheme is refused
  instead of getting H60A6's frames, and an explicit H6061 panel count of 0 is rejected even for a Kelvin
  tint (it became 23 panels). Modeled from the decompiled app; not hardware-verified.
- 0xAC status burst framing follows `AbsController4BleSingleSendMultiBack`: a burst is complete only when
  every packet its first chunk declares has arrived, so a burst that lost the chunks before the 0xFF chunk
  no longer splices it in at the wrong offset and a lone 0xFF chunk isn't read as the first; a one-packet
  reply is complete by itself and is trimmed to its declared length, the last packet adds only its declared
  byte count, and an impossible last-packet length is rejected. Modeled from the decompiled app; not
  hardware-verified.
- Read-back values as the app reads them: 0xAC zone power is bit 0 of each zone byte
  (`ParseUtils.getBit2Boolean`); a 0x07 block whose selector isn't basic (0x10) or Wi-Fi (0x11) is stepped
  past even when its body doesn't parse, and the Wi-Fi block's versions no longer become the device's
  (`Compose4BaseInfoSingleRead.w`); gradual (`aa a3`) and H6047 bar power (`aa 36`) are on only for a byte of
  1 (`ControllerGradual4BleWifi.h`, `ComposeLightController.parseValidBytes`); device-reported brightness is
  clamped to 0..100. Modeled from the decompiled app. H6047 bar power is live-verified (2026-10-15, macOS:
  left bar off, then on, read back per bar); the other rules are not hardware-verified.
- H6006 / H6008 / H6004 / H6188 mode report decoded as the app does (`ModeParseStrategyConfig.a` via
  `OpInfo4Detail.parseModeValidBytes`): sub-modes 0x15, 0x0b, 0x02 and 0x0d all read `[r, g, b][kelvin
  s16be][tint]`; a Kelvin > 0 is CCT, 0 is the RGB, and a negative one shows the tint as RGB. `aa 05 15 01 0a
  8c` now gives RGB (1, 10, 140) instead of 2700 K, and a 0x0b / 0x02 report updates the colour. Modeled from
  the decompiled app. Live-verified on an H6006 and an encrypted H6008 (2026-10-15, macOS): their 0x0d reports
  arrive as `[r, g, b][kelvin][00 00 00]` and decode to 2700 K after a CCT write and to RGB (16, 32, 48) after
  a colour write. 0x15 / 0x0b / 0x02 reports and the H6004 / H6188 are not hardware-verified.
- H6061: an `aa 05 15` report no longer clears the known RGB colour or stores a nonsense Kelvin (e.g. 511 or
  65454), since `CubeNewInfo4Detail.U` decodes only the 0x0d report; and that report's Kelvin is a signed
  short (`BleUtil.getSignedShort`), so 0x8000 or more reads as RGB, not a 32768 K colour temperature.
  Modeled from the decompiled app; not hardware-verified.
- Other mode reports: on H61A8 an `aa 05 0d` report no longer sets an RGB colour (it is a
  `SubMode4Color` `[gradual][kelvin s16be]` report, see the H61A8 entry above); H6062 reads 0x0b / 0x02 / 0x0d
  with the `SubMode4Color` parse (`OpInfo4Detail.parseModeValidBytes`); H6641 and H6047 ignore 0x0d
  (`Info4Detail.parseModeValidBytes`). A scene report clears stale colour state and a colour-temperature
  report of 0 clears the Kelvin. Modeled from the decompiled app. A colour-temperature report of 0 clearing
  the Kelvin is live-verified on an H60A6 (2026-10-15: 4000 K, then red, then `aa 05 15 01 00 00` read back no
  Kelvin); the rest is not hardware-verified.
- Plugs follow the app per goodsType. The plug profile is split by goodsType into H5080/H5083/H5085,
  H5082/H5089 and H5160/H5161, so `profile_for` returns three different plug profiles. Relay read-back: bit
  0 on H5080/H5083/H5085, any bit on H5082/H5089, bits 0-2 on H5160/H5161 (`BleOpV1.onEventSwitchV2`,
  `EventSwitchV2.i`, `BleOpV4.Z`; 1.3.0: any set bit meant on). Advertised on/off: bit
  0 on H5080/H5083/H5085 and any bit on H5160/H5161 (h5080 `Model.A`), bit 0 or 1 on H5082/H5089
  (`Model4Plug2.A`); before, only a byte of exactly 1 meant on, so 0x11 read as off. Modeled from the
  decompiled app; not hardware-verified.
- Advertisements: on/off is read only from broadcast headers 2 and above (`BleUtil.parseBleBroadOnOff`), so
  v0/v1 adverts no longer flip power, and `identify.parse_broadcast_onoff()` returns None for a header byte
  (the company-id low byte) below 2 or above 0x7F (negative as the app's signed byte); the advertised on/off
  is ignored for 15 s only after an on/off read or a command, not after an empty, failed, brightness-only or
  mode-only read; an advertised power change keeps zone and bar power consistent, so a later H6047 bar write
  no longer lights an untouched bar; and a local name ending in `_` gives the app's SKU
  (`BaseBleProcessor.parse`). Modeled from the decompiled app; not hardware-verified.
- Per-segment colour read-back: H6641 derives its piece and group count from the IC count as the app does
  (clamp 41..70, ceil(IC / 5) pieces, at most 4 reads; `ReadIcController`, `H61D3Support.e`), so it shows no
  phantom segments and never walks 255 groups; replies are placed by the group requested, on H61A8 too (see
  the mechanism-B entry). An `aa 40` IC count of 0x1000 or more (read signed) skips the segment walk, as
  the app never stores it (`ReadIcController.Companion.a`). A custom profile with `color_readback_live_ic` and
  no `color_readback_ic_range` uses the IC count unclamped. Modeled from the decompiled app; the H6641 change
  is not hardware-verified. The H61A8 15-segment read-back still assembles on hardware (2026-10-15, macOS).
- Device-info reads are tried again on the next poll when the device answered none of them and that poll's
  state read got nothing either. 1.3.0 marked them done after one unanswered attempt, so a device first
  polled while silent (wrong cipher, out of range) never reported its versions. A late Wi-Fi info reply can
  no longer overwrite the firmware or hardware version. Modeled from the decompiled app; not
  hardware-verified.
- State: segment colour, segment brightness and zone colour writes clear the active scene, matching bar
  Kelvins clear a stale RGB colour, concurrent whole-device power or scene writes and H6047 per-bar writes
  no longer undo each other, and `set_zone_power` on H60A6 replaces `state.zone_power` instead of editing
  the dict in place. Not hardware-verified.
- A scene's upload and its activation are one unit, as the app sends them in one composed controller:
  neither a poll nor a concurrent scene write can land between them. `set_scene_by_name` loads the bundled
  catalog off the event loop, `update()` warms it, a catalog that exists but can't be read raises
  `GoveeBleError` instead of `OSError` / `UnicodeDecodeError`, and `active_scene` is None on a device
  without `SCENES`. Modeled from the decompiled app; not hardware-verified.
- Frame capture: creating one (`frame_log=` / `GOVEE_FRAME_LOG`, built on the event loop) no longer touches
  the filesystem. The directory check runs on the writer thread and disables only that capture, with one
  warning (other captures on the same path keep writing). It warns once when it appends to a group- or
  world-accessible file (POSIX; the mode is kept), reports a write failure again after the path recovered
  and a full queue on each new overflow, and registers its exit-time flush at the first capture, not import.
- Cloud: `CloudDevice`'s repr leaves out the BLE `secret`, so logging the device list can't leak the
  account-lock key. A JSON null `sku` / `device` / `deviceName` no longer becomes "None", huge or
  non-integral `pactType` / `pactCode` / `goodsType` are ignored rather than raising `OverflowError` or
  being truncated, and a non-integral `scenceParamId` or a body nested too deeply for the JSON decoder
  raises `GoveeCloudError` instead of `OverflowError` / `RecursionError`.
- `govee-ble-analyze` reassembles 0xAC bursts per device, so overlapping bursts from several devices no
  longer produce phantom "TLV type not modelled" issues and exit code 1. A double-delivered burst isn't
  malformed, a burst missing interior or final chunks is, a poll that lost its 0xFF chunk is no longer
  folded into the next poll with the same first chunk, and a TLV that won't parse is reported as a gap.
- `govee-ble-analyze` no longer reports every burst of a redacted field-test capture as "malformed
  (dropped/merged frames)" (older captures, whose first-chunk header was zeroed, are judged by chunk index).
  `analyze()` skips and counts invalid records instead of raising `ValueError`, a deeply nested JSONL line
  or a 1-byte `ac` record no longer crashes the CLI, `--from-frames-log` keeps the logger name out of the
  address, and an unrecognised `dir` reads as `?`.
- Field test, report: error messages (failed sends, read-backs, restores, aborted runs) no longer contain the
  device's Bluetooth address, Wi-Fi MAC or the MAC digits of its advertised name. Every error string, and the
  report as saved, goes through one scrubber that masks the address in any form (BlueZ D-Bus paths included),
  any other MAC-shaped text and Govee-shaped names (`ihoment_H6188_xxxx`). Snapshots always redact the device
  id and BLE MAC too. A device identified as one SKU (`ihoment_H6006_6188` is an H6006) no longer matches
  another SKU's plan through the digits in its name, and Ctrl-C at a prompt no longer hangs the tool.
- Field test, capture: the frame capture keeps no wire bytes (the ciphertext and the `e7` handshake, from
  which the session key can be derived) and zeroes the `ac` status payloads like the device-info replies,
  keeping only each burst's 7-byte first-chunk header so `govee-ble-analyze` still spots a lost chunk. It is
  redacted only after `framelog.flush()` (at once if that fails, never left raw), and the report and capture
  go through a fresh temp file and an atomic rename, never a planted symlink.
- `tools/decrypt_btsnoop.py` decodes the exact-length 0xA3 / 0xA4 / 0xA6 multi-packet frames the app sends
  (MTU-sized and short; `MultipleControllerCommV1.makeSendBytesMtu0xA6`) instead of dropping them, and
  dumps 0xA6 by default (`--dump a1,a3,a4,a6`; `--starts` includes 0xA6). It takes a session key only from
  an `e7 01` reply after the host's `e7 01` on the same connection and keeps keys per connection; it and
  `btsnoop_mtu_scan.py` refuse captures that are not H4 (datalink 1002) instead of reporting nothing.
- `tools/fetch_scenes.py`: `--audit` honours the SKUs given, shows their real scene versions and puts every
  scene in one bucket; a re-fetch replaces stale upload metadata and picks up changed public blobs (keeping
  a `--resolve`d blob only over the 0xFF stub); a corrupt catalog or failed write fails that SKU instead of
  the run. `tools/power_semantics_probe.py` restores the H6047 with its own `33 36` bar frame and prints
  `RESTORE FAILED` when a restore isn't acked or the starting state was never read; `h60a6_live_check.py`
  matches segment colours by index and `--capture` uses `frame_log=`.
- `tools/field_test`: the report and frame capture meant for a public GitHub issue no longer leak the
  Bluetooth address. Error text (most transport errors start with the address) is scrubbed of the address in
  any case and form, the Wi-Fi MAC derived from it, every other MAC-shaped token (BlueZ's `dev_XX_XX_...` path
  included) and the MAC digits of Govee names (`ihoment_H6188_xxxx`), and the whole report is scrubbed again
  as it is saved. The capture keeps the first 0xAC chunk's header, so a lost chunk is still detectable, and
  zeroes every data byte.

### Security
- The wire layer masks the account-lock secret in its own log lines and exception text. This is defence in
  depth for callers that hand `wire.parse` a frame directly (tools, tests, integrators). Neither site fires
  from the shipped runtime: the connection drops a notification that isn't 20 bytes or fails its checksum
  before any parse, and `Device` only hands `request_command` the `aa` reads it builds.
  - Before, a reply that didn't parse was logged as its full hex, so a malformed `aa b1` secret reply
    showed the secret, and a `33 b2` frame too short for a write ack its first secret byte.
    `parse.request_command`'s error named the frame it was given, so a `33 b2` secret write handed to it
    carried the secret into the exception message.
  - Every frame logged or named in `wire/` now goes through `wire.redact.shown_hex`, which masks a
    `33 b2` / `aa b1` frame by its two header bytes, so a frame whose body doesn't parse is masked too. For
    such a frame the reader's exception is shown by its type only (`wire.redact.shown_error`), since a
    `valid` failure's message quotes the field it read; that applies to `describe`'s parse-error issues
    as well. The only bytes still shown are field values, never whole frames.
  - The masking helpers moved from `transport.connection` to `wire.redact` (`carries_secret`,
    `mask_secret`). The frame capture, the connection's bad-checksum log and `tools/decrypt_btsnoop.py`
    use them from there.
  - Logging, exception and analyzer issue text only: no byte sent and no decoded state changes.
- The connection's frame capture and frames log fail closed. This was a real leak:
  - A notification that arrives while the session isn't ready used to be recorded in the clear. That
    covers frames between subscribing and the handshake, and frames after the teardown cleared the
    session. A late or repeated `aa b1` reply landing while `stop()` tears the session down
    (`read_secret` followed at once by `stop()`, the config-flow and reauth shape) was logged and captured
    unmasked. That was shown with the test fake, not seen on hardware (not live-verified).
  - Such a frame is now masked by its header on a plaintext link. On an encrypted link, where it can't be
    checked, only its length is kept: its wire is blanked and the log line says `withheld`. That
    includes the e7 handshake frames: their bytes plus the public PSK give the session key, so a capture
    no longer lets anyone derive it.
  - A secret frame's ciphertext is blanked whole. Its first two bytes, kept before, are AES over the block
    that holds the secret: a check on a guess.
  - Logging and capture text only: no byte sent, nothing that reaches the RX queue or the handshake, and
    no decoded state, return value or connection behaviour changes.

### Spec
- dreamcolorlightv1 goodsType 19 (H61A8) gets a `client_profile.by_goods_type` entry. Its colour page sends
  the 15 01 forms, not 0x0b (Config.x[19] = Py -> Config.Yt -> Bytes.h / Bytes.r), so `color_scheme: h60a6`.
  `color_functions: [segment_brightness, color_temperature]` gives the two colour-page flags with consumers
  (Config.I gO via Config.e). `kelvin_range` is [2000, 9000], and `kelvin_by_firmware` gives [2700, 6500] from
  hardware 3.08.01 (KelvinConfig.Config.z2, a plain string compare). The schema takes these four keys per
  goodsType. New opcode `segment_brightness_write` (33 05 15 02, Bytes.G), traced for 19 only. Bench, H61A8
  SW 3.03.18 / HW 3.01.10: both 15 01 forms are acked, and `aa 05` reads 15 01 00 00 after a CCT write.
- New `govee_state` instance `app_empty_mode_fails` (`FIRMWARE-SOURCED`, `PROCEDURAL`): an empty TLV 0x05 value
  where the mode decode has `empty_fails` (h60ax goodsTypes 235, 302, 303, 346, 356, 370, 373 and 390). H60A6
  SW1.00.41 sends it on purpose in mode 0xA0 (music: a 33 05 13 write or an A5 02 83 mic colour push) and in every
  mode but 04 / 0a / 0d / 13 / 15. The app's whole status read fails on it (Info4Detail.Q); a client reads past
  it, with the mode unknown, and still fails on every other `app_read_fails`. `app_read_fails` now names it as one
  of its terms; its results are unchanged. H60A1 SW1.02.20 builds no 0xAC burst, so it never sends the value.
- New `command_forms` selectors `f_40` (`form_40`: `unknown`, `ic_pair`, `one_count`, `device_count`) and `f_a3`
  (`form_a3`: `unknown`, `gradual`) say how a status screen reads TLV 0x40 / 0xA3 (rule 4 c). `govee_state`
  `app_read_fails` flags those values only where the screen reads them: H60A6, whose screen ignores both, no
  longer flags an empty 0xA3 or a short 0x40, and the IC pair reader's under-3-byte and byte-order failures are
  now flagged (new `ic_count.app_pair_fails`, ControllerIcNum.e). `ic_total` now needs 3 bytes. The values stay
  typed under every form. `devices.yaml` sets the facts on 23 goodsTypes in 15 families and the goodsType 69
  pact (2,1) branch; `govee_reference.make_profile` takes the two new keywords.
- Each `seg_mask` parent that passed a literal cap now names it as a `len_cap` value instance, as `op15_color`
  does (rule 4 b): `op15_brightness` 14 and `color_0b_rgb_mask` 13 (the app cuts a longer mask, Bytes.G / Bytes.d),
  and `op15_gradient` 6 (Bytes.n copies the mask with no cut, so a longer one throws). Instances only; the bytes
  read and written are unchanged.
- New `spec/firmware.yaml` and `spec/firmware.schema.json` give device behaviour, state and timing per
  firmware image, as data, so a spec-driven simulator builds every reply, push and advert from spec
  objects with no hand-built frames. The first image is H5083 SW1.00.30 (`h5083_sw1_00_30`). It covers:
  - the shared write layer and the pair-gated dispatcher: drop rules, every answer, its effects and
    whether they apply before or after the reply, and what goes on to the dispatcher;
  - the E7 session, the AA B1 button window and 33 B2 pairing, timers (records and used flags apart),
    delays, 33 B5, the A1 11 Wi-Fi and A3 IoT uploads in their shared buffer (the credentials decoded
    with `govee_upload`), and the AB 02 pact report;
  - the state model, the pushes (AA 01, EE 11, A3 08), the link events and the 1 s clock task (the
    10-11 s heartbeat and 61 s pair-key disconnects, delay expiry), and scenario events (button, timer,
    cloud);
  - the advert and the scan response.

  AB 01 04, 66 01 and 3F have no spec reply type and are marked unmodelled. The H5083 profile gains the
  firmware-sourced form `f_14: wifi_mac` for AA 14. A throwaway simulator built from the file alone passed
  every H5083 scenario.
- `lint.py firmware` checks `firmware.yaml` against its schema. Every type path, node field, switch case
  (picked by a sibling literal or the image profile's form), enum literal, match path, route, state name,
  let, push, event arg and expression name must resolve against the `.ksy` types and the image. It also
  checks the profile against devices.yaml, the doc-refs, and duplicate or YAML 1.1 boolean keys.
- `govee_state` `pact_state` cites `H5083 FUN_9b031b80`; `ble_msg_get_adv_pro_type` is not an H5083 symbol.
  FIRMWARE_VALIDATION §6 points the H5083 bodies to `firmware.yaml`.
- Suite S closes the ways around its one-path rule that a review found.
  - `roots`: the nine tops are pinned with their labels, so a new file top fails until the lint lists it.
  - `roots`: a `CONTEXT` top has no seq or instances, and a `LIBRARY` top has no seq, params or instances.
    No other type in the `CONTEXT` file reads bytes (a seq, or an instance that is not a `value:`).
  - `roots`: every used type that reads bytes must be read from a root, not only reached as a param type,
    so a param can't hide a parser.
  - `roots`: a type name carrying a `typed`, `safe`, `sized` or `frame(s|d)` twin word fails; the root
    `govee_ble_frame` is exempt.
  - `banned` now also scans an instance, type or switch case whose id or key is `doc` or `title`. Before,
    it skipped those as doc text.
  - `meta`: every import must be the bare stem of another `spec/*.ksy`, so ksc can't pull in an
    unlinted file.
  - The current spec passes unchanged: 9 ksy, 205 types, 0 errors.
- `spec/` now keeps CLAUDE.md rule 7 ("Cite, don't copy") and its one-builder-per-variant rule.
  - Some 450 decompiled-code fragments in the docs, the `devices.yaml` notes, the catalogue docstrings and
    the reference comments are rewritten in spec terms. Each keeps its citation and meaning. The rewrites
    cover casts like `(byte)88`, `new byte[]{...}` and Java array literals; decompiler names like `bArr`,
    `param_1`, `z2` and `LAB_...`; and pasted Java and C statements, ternaries and assembly. Firmware
    `FUN_...` symbols stay, because they are the `IMAGE symbol @0xaddr` citation form.
  - The `govee_upload` H60A6 DIY sub-effect fields lose their decompile identifiers (`param_1`, `t0`, `r`,
    `q` and the like). The new names were traced through H60A6DiyParse and the labels on the DIY screens:
    `speed` in every sub-effect, plus `laps`, `reverse`, `colorful`, `count`, `bead_count`,
    `tail_length`, `duration`, and LiuDong4Area's `offset_0` / `offset_1`, which the app sets from the
    direction. A byte the app gives no meaning gets a neutral `untraced_N` that says so in its doc. Only
    ids change, and nothing read them.
  - `build_op15_color_ver5` duplicated `build_op15_color_v5` (`33 05 15 01`, colour version 5), and it
    sized the mask from the mask's bit length instead of the position count. It is removed, and its
    whole-frame vectors join v5's self-check.
  - `govee_reference.build_op15_color` duplicated `build_op15_color_bytes_h` (its variant 12) and
    `build_op15_color_temp_bytes_r` (variant 11). It is removed, along with the helpers only it used,
    `op15_mask_width` and `op15_mask_from_int`. Its variant 1, the H60A0 basic form
    (SubModeColorV1.getWriteBytes opType 1), is the new catalogue builder `build_op15_color_basic`.
    `tests/test_wire_build_vs_spec.py` now compares against the catalogue builders, which give the same
    bytes for all 2079 inputs it feeds.
  - The catalogue has 462 builders and 759 self-checks, and `OPCODE_COVERAGE.md` indexes exactly those.
- The `govee_commands` builders lose their decompile-style parameter names (`b0`, `b2`, `c0`, `t`, `f`, `i`,
  `p1`, `t1`, `field_a`, `a_flag` and the like). Each new name was traced from the app code the builder
  cites: `build_low_blue_light` `red` / `green` (the app names the three value bytes red, green and blue;
  `level` is the blue one), `build_video_color_cali` `yellow, blue, purple, red` (its calibration sliders),
  `build_ai_filter` `red, green, blue, saturation, filter_code, duration_minutes`, `build_splicing_config`
  `count, position`, `build_daysync_node` `hour, minute`, `build_diy_create_gif` `diy_code`,
  `build_color_tem_type` `tem_type`, `build_pick_mic` `mic_source`, `build_video_h6101` `dynamic,
  part_screen` and `build_video_h6104` `part_screen, sensitivity`. Values the app only copies from a reply
  or always sends as 0 get `untraced_N`, and their docstrings say so: `build_set_ai_action(_v2)`
  `untraced_0` / `untraced_1` and `build_splicing_sub_device_mac` `untraced_0`. The daysync upload builders'
  local helpers `j` / `k` become `unsplit_package` / `split_package`. Bytes are unchanged, and nothing in
  `src/` or `tests/` passed these parameters by keyword.
- `build_diy_create_gif_v1` duplicated `build_diy_create_gif` (`33 86 01`, NOTIFY_DEVICE_CREATE_UPLOAD_GIF):
  same parameters, same bytes, and the same and only app sender, DiyEdit4DeviceCreateGifActivity.l2. It is
  removed. Its citations and whole-frame vector join `build_diy_create_gif`, whose two overlapping
  self-checks become one. The catalogue has 461 builders and 757 self-checks, and `OPCODE_COVERAGE.md`
  indexes exactly those.
- `33 05 15 05` (op15_kelvin, H60A0): the Kelvin is now spelled `u2be` (it already read high byte first; the
  generated code is unchanged). The app writes it high byte first (ControllerBuildH60A0.d), and no firmware image
  handles op 05: on H60A6 it falls to the default branch, which logs 'Unknow msg'. The citations point at that
  binary path. The catalogue keeps one builder, `build_op15_kelvin_05`. `build_op15_color_temp` and
  `build_op15_color_temp_05` were byte-identical duplicates and are removed (759 self-checks). A reference check
  reads `33 05 15 05 0a 8c` back as 2700 K. The last decompiler-style quotes in devices.yaml notes are rewritten in
  spec terms.
- The spec is restructured into nine Kaitai files that compile in one `ksc` call
  (`-t python -w --python-package .`). They replace `govee_ble.ksy` and `govee_adv.ksy`, and every documented
  fact and citation of the old files has moved to a new home.
  - Shared files:
    - `govee_common.ksy` (LIBRARY): the shared enums (`command`, `pro_type`, `sub_mode`, the 18 `form_*`
      selectors, `color_decode`, `music_decode`, `group_count_rule` and others) and the context-free leaves
      (`rgb`, `level`, `seg_mask`, `echo` and others).
    - `govee_profile.ksy` (CONTEXT): `govee_profile(forms, counts, group, power, mode, mode_status)`, the device
      facts a reader needs. Value 0 of every selector is `unknown`, which leaves an overloaded body as raw bytes.
    - `govee_state.ksy` (SHARED PAYLOAD): `govee_state(command, source, profile)`, one type for an `aa` reply
      body and a 0xAC TLV value.
  - Six STANDALONE roots, one per byte source:
    - `govee_ble_frame(direction, profile)`: one GATT 2b11 write or 2b10 notification, `[pro_type][body]
      [checksum]`. `route` (direction and proType) picks the body; `checksum_kind` says xor, sum8 or none.
    - `govee_status_stream(profile)`: one joined 0xAC status burst, read as TLVs and a 0-2 byte tail.
    - `govee_upload(kind, form)`: a joined A1 / A3 / A4 value or a decoded cloud scenceParam.
    - `govee_manufacturer_data`: the 0xFF manufacturer AD payload (was `govee_advertisement`).
    - `govee_scan_record`: a raw advert or scan-response PDU.
    - `govee_bgc_info`: the GATT 2b12 value.
  - The rules:
    - Each message has one type path.
    - Context is params only: no `_root`, `_parent`, `io` / `pos` instances, `process` or opaque types.
    - Every message ends with a lossless `rest` or a named data field.
    - Every enum is applied to a field.
    - Every form the app reads or a firmware sends reads without raising; the app's rejections are `app_*`
      instances.
- `devices.yaml`: every family with a `client_profile` gets a `wire` block, from which the library builds
  `govee_profile`.
  - The block holds `forms`, `counts`, `group`, `power`, `mode` and `mode_status`, with `by_model` /
    `by_goods_type` / `by_pact` / `by_firmware` overrides.
  - It adds `relay_heartbeat_on_mask`: 0x03 for goodsTypes 50 / 307, 0x01 for 43, 0x07 for 90.
  - `ksy` and `mode_0d_kind` are removed; the `govee_common` enums hold their facts.
  - `devices.schema.json` gains the matching `$defs`, with the selector enums taken from `govee_common.ksy`.
- New `spec/checks/run.sh` (Suite S).
  - It runs ksc for python and java with `-w`, then javascript, and fails on any compiler output.
  - Then `checks/lint.py` checks the meta keys, the banned constructs, the six labelled roots and
    reachability, that every enum is applied, schema and `mode_color_decode` enum equality, and the doc-ref
    format.
- `govee_reference.py` uses the new classes.
  - `parse_frame(frame, direction, profile)` reads any frame length and checks the trailer by `checksum_kind`.
  - `parse_status` returns `govee_status_stream`.
  - New `make_profile(**facts)` and `checksum_ok`.
  - `parse_color_group` is removed.
  - The docs (`USING_THE_KSY.md`, `OPCODE_COVERAGE.md`, the GATT, firmware and scene-upload notes) name the
    new files and types.
- Review fixes to the new files:
  - `33 05 15 05` has three app layouts, so it gets the selector `f_15_05` (`form_15_05`): `kelvin` [kelvin
    u16be] (H60A0, SubModeColorV1 opType 2), `side_brightness` [side][value] (Bytes.H, goodsType 80) and
    `gradient` [3 x RGB][mask] (Bytes.n). The Kelvin was read little-endian; the app writes it high byte
    first. `govee_commands.build_op15_color_temp` now writes it big-endian too. devices.yaml sets `kelvin`
    on goodsType 188 and `side_brightness` on 80. The `op15` enum docs give the right opTypes (0x05 is
    opType 2, 0x00 is opType 3).
  - A 0x15 01 colour write, and a 0x0b rgb_mask write, stay raw (bytes in `rest`) when the profile has no
    segment count. Before, any set mask bit raised.
  - `checksum_kind` builds its enum from an integer, so the generated Java compiles.
  - `ic_count` (aa 40 / TLV 0x40) reads the second [IC][segment] pair at bytes 3..5 (`ic_count_2`,
    `segment_2`), with `segment_signed`, `has_second_pair` and `ic_total` (the sum the goodsType-375 and
    H6609 / H6250 screens use).
  - `color_group` flags a group the TLV screen cannot place (`app_rejects_group`, `app_read_fails`) under rule
    `fixed` too, when `num_pieces` is known. devices.yaml gives h6047 goodsTypes 277 / 288 / 298 their piece
    counts (12 / 24 / 22).
  - An aa a2 colour-only group (`f_a2: color_group_rgb`) always reads 3-byte records. Straightfloorlamp
    goodsType 30 sets that form, for its reads below the firmware gate.
  - Doc fixes: `f_ee_30` unknown still reads the EE 30 bytes; the plug heartbeat masks of goodsTypes 43 / 90
    are verified; two devices.yaml / schema cross-references name the new types; seven docs that copied
    decompiled code are prose now. In Java an unknown proType still reads as null (ksc 0.11 cannot put an
    imported enum on a value instance); the frame doc says so.
- Docs trimmed to essentials (docs only; generated code and bytes unchanged). The nine `.ksy` files go
  from 9405 to 6758 lines, `devices.yaml` from 3788 to 3513 (comments only), and `devices.schema.json`
  stays at 876 (shorter `description` strings): 14069 to 11147 lines in all. ksc's output differs only in
  docstrings, plus the order of two sibling imports in `govee_scan_record` and `govee_state`.
- `devices.yaml` striplight `color_temp_write` cites the BLE builder the colour-tab Kelvin slider runs
  (`ColorViewModel.U` -> `ColorModeControllerConfig.c` -> `Config.Yt` -> `Bytes.r`). It used to cite
  `ColorControllerConfig.b`, which builds the IoT command. The frame bytes are unchanged.
- `devices.yaml` dreamcolorlightv1: a comment records that `color_scheme: h61a8` (the 0x0b write) describes
  only colour version 3 (17 / 18 below their firmware gates, old H6163 / H6117, part brightness off). The 13
  goodsTypes 71 to 262 send the h60a6 0x15 frames. `color_scheme` is per family, so the value stays.
- LOCKSTEP (merge together with the library edits in spec/docs/LOCKSTEP_FRAME_AUDIT_E.md): the section-E forms
  that change what existing readers return.
  - `single_command.params` types 33 B2 / A3 / 14.
  - `mode_read.selector_or_sub_mode` takes `enum: sub_mode`.
  - `status_tlv` 0x07 reads `status_device_info`.
  - `status_tlv` 0x05 reads `mode_read` over the exact value, so `cct_read_reply.kelvin_right` is read at
    `_io.size >= 5`.
  - `color_group_status.records` is `(len - 1) / 4` plus `trailing`.
  - Without the library edits, 35 tests fail on each library branch (32 from the B2 / A3 builders, 3 from the
    0x07 walk). With them, both branches pass.
- `devices.yaml` now catalogues the light families and goodsTypes that Govee Home 7.7.02 routes but the file
  did not model: 148 more light goodsTypes (113 -> 261 non-zero) and 205 more SKUs.
  - How it was traced and checked:
    - Each family was traced in the complete 7.7.02 decompile from its routing (PFD module, SubLib keys,
      `Model.t`) through the control screen's connect reads, mode parser and colour builders, with every claim
      cited `path:line`.
    - Each trace was then re-traced independently from both ends before it went in. Corrections from that pass
      are applied, and every Java citation resolves (4244 citations).
  - 40 new families:
    - pact_rgbiclight: `chassislightv1` (11 / 162) and `h7090` (44 / 127).
    - pact_bulblightstringv1: `bulblightstringv2` (35 / 148 / 49 / 91).
    - pact_hollowlamp: `straightfloorlamp` (30 / 69 / 312 / 377).
    - pact_h6061: `h6069` (284). The cubelight screen sends it whole-device 0x0d frames, so it is its own
      entry.
    - pact_floorlamp: `h6071` (57 / 67), `h60b1` (300), `h60b0` (301).
    - pact_h6079: `h6079` (156 / 209 / 376), `h6020` (210), `h6022` (249), `h1741` (365).
    - pact_h6840: `h6840` (278 / 325 / 381 / 382) and `h6609` (173 / 181 / 260 / 362 / 374 / 378 / 392 / 398).
    - pact_h70b1: `h70b3` (229 / 230 / 231 / 255 / 256), `h6800` (272), `h70b6` (329 / 341), `h70dx`
      (267 / 268 / 269).
    - pact_h6099: `pact_h6099` (191 / 203 / 253 / 273 / 327 / 336).
    - pact_outdoorlight: `h7040` (36), `h7080` (45 / 55 / 56), `h7056` (304), `h7086` (293).
    - pact_h7050: `h7050`, `h7057`, `h1630`, `h7045` and `h7020`, all 42 of its goodsTypes.
    - pact_h7075: `h7075`, `h7078`, `h3751`, `h7072`, `h7076`, `h707a`, `h3700`, all 9 of its goodsTypes.
    - pact_fanlight: `fanlight` (359 / 368), including the fan opcodes (0x31).
    - pact_h6092: `h6092` (13 projector goodsTypes).
    - pact_h6630: `h6630` (285 / 286). Its control screen is IoT-only.
    - `h3a5x` (386) and `h682021` (280) are `untraced`, as h604a is: their feature splits are not in the
      capture.
  - Further goodsTypes and SKUs on existing families:
    - `striplight`: H610A (80) and H610B (114) run the same screen and parser as H6062.
    - `h6061`: 96 / 97 / 121 / 163 / 228. Panel counts and CCT mask widths differ per goodsType.
    - `dreamcolorlightv1`: the 13 further SubLib4BleIot keys, 71 to 262, all `kelvin`. AdjustAcV3 is never
      launched in 7.7.02.
    - `bulblightv3`: the R-prefixed SKUs (R1501, R14A1, R14C2, R1401, R14C0, R14C1). They route by goodsType
      to the same screen.
    - `h61d3` 263: the 0xAC status read and its TLV 0xA5 record.
  - New strategies:
    - `rgb_kelvin_15`: H6071 / H6075 read the rgb_kelvin_0d body on sub-mode 0x15.
    - `kelvin_14_15` and `gradual_14`: straightfloorlamp's colour sub-mode is 0x14.
    - `bri_rgb_pair_15`, with the new decode `bri_rgb_pair`: H7061's two [brightness][r, g, b] head records.
  - `devices.schema.json` accepts the new strategy and decode names, sub-mode "0x14", and `power_live`
    `heartbeat_aa33`, the aa 33 left / right heartbeat of H610A.
  - `encryption: secret_key_pairing` is set only where the control screen presents 0xB2 on connect: h6071,
    fanlight from firmware 1.00.20 / 1.00.11, and h6630's square path. A secret used only by pairing, group or
    room control is `none`, as for h6061 and striplight.
- Frame-audit section E: the spec forms the library still builds or decodes by hand. Each was traced in the
  Govee Home 7.7.02 decompile and checked by an adversarial reviewer. Additive only: 9 standalone types wired
  into no dispatch, and value instances on existing types. No `pos` instances, so a written type is never
  re-written from them. No enum is applied, no `switch-on` case is added, and no existing field or expression
  changes. The harness (2,006,142 cases) shows 0 differences, and both branches' suites pass (2053 each).
  - Requests:
    - `read_request_sized(selector_len)` and `read_request_frame` write `aa <cmd> <selector...>` with the zero
      fill the app's frames carry.
  - Segment masks:
    - `segment_mask(segment_count, mask_cap)` gives the app's width rule (2 bytes up to 16 segments, else
      ceil(n/8), capped per body) and the per-segment bits, LSB first.
    - `op15_brightness_wide.mask_cap` (14) and `color_rgbic_0b_wide.mask_cap` (13).
  - Writes:
    - `single_command_typed` types every 0x33 write body, including `33 B2` secret, `33 A3` and `33 14`
      gradual. `single_command` itself is unchanged; the in-place cases are in the lockstep branch.
  - Mode reports:
    - `mode_read.sub_mode` is the `sub_mode` enum view of the byte. `mode_read.is_submode4color` covers
      0x15 / 0x0b / 0x02 / 0x0d, as `OpInfo4Detail.x` hands them to SubMode4Color.
    - `mode_read_params` exposes the raw params.
    - `cct_read_reply.is_cct_positive` and `mode_color_0d_typed.is_cct_kelvin_positive` give the default
      SubMode4Color rule, signed kelvin > 0.
  - Status bursts:
    - `color_group_status_counted(piece_count, group_size, has_brightness)` counts a 0xA5 group's records
      from its group_index, as the app does.
    - `status_tlv.total_len` and `status_device_info.app_decodes` (selectors 0x10 / 0x11).
  - The `aa a5 <group>` direct reply:
    - `color_group_direct_frame(record_count)` reads it, keeping the padding so it round-trips.
  - Plugs:
    - `switch_read_reply.relay0_is_on` .. `relay7_is_on`.
    - `any_relay_is_on`, `any_relay_0_1_is_on` and `any_relay_0_2_is_on`: the per-goodsType read and
      heartbeat rules.
  - IC count:
    - `ic_segment_read.ic_count_byte_order_suspect` (signed count >= 0x1000) and `ic_count_checked`.
  - Library-scene upload validators, so a failed check can make a scene activate-only as the app does:
    - `scenes_graffiti_check` mirrors ShareDiy.isValidGraffitiBytes exactly: size >= 9, the 0x01 lead and
      the group-header walk.
    - `rgb_scene_value.is_valid` mirrors ScenesRgb.isValidProtocolBytes.
  - Held back as lockstep (they change what existing readers return): the single_command params cases,
    status_tlv 0x07 -> status_device_info and 0x05 -> mode_read, and the sub_mode enum applied to
    mode_read's field. They are measured on a separate branch for the library owner.
- `devices.yaml`: non-colour data and citations that the complete Govee Home 7.7.02 decompile contradicts
  are corrected. Every item was re-read in the code, and every Java citation in the file now resolves
  (`file:line` within the file).
  - `delivery`: 42 families move from `base_apk` to `feature_split`. 7.7.02 ships them in on-demand
    pact_* splits (pact_rgblight, pact_rgbiclight, pact_bulblightstringv1, pact_hollowlamp, pact_plugnew,
    pact_h705a, pact_h70b1, pact_tvlightv4, pact_h6099, pact_h604a), mapped by goodsType or SKU in
    `base2home/pfd/Config.b()`. Each line names its module and Config line. h1162 stays `base_apk`: no
    module claims its goodsTypes. The schema enum is unchanged; its description now says where the mapping
    lives.
  - h7004 `red_blue` is cmd 0x0d with payload `[red, blue]`, not 0x01. The plant screen writes
    `33 0d <red> 01` and reads `aa 0d` at connect. The 1/2/3 codes of the legacy SubModeRedBlue classes
    are never written. h7017 gains the same opcode.
  - h6185: the `mic` opcode entry is removed. Mic is mode sub-mode 0x05 (`33 05 05 01`), and 7.7.02 has
    no rhythm-point controller.
  - tvlightv1: the sub-modes were right, but only the legacy `Mode` (0x0a / 0x0d) was cited. Scene 0x04
    and music 0x0e are what the goodsType-12 control screen writes, now cited. `segment_limit` (0x0e,
    `ControllerLimit`) gains its payload and read.
  - h61d3: the connect switch is `VM4Light.d0` on `H61D3Support.l0` (goodsType 263), not `f0`, which is
    goodsType 299. The per-group read is pieces = ceil(IC / 5), groups = ceil(pieces / 4)
    (`VM4Light.w5` / `x5`), not "group count = ceil(IC / 5)". The library already reads 4 records per
    group.
  - bulblightv3: `checkSupportH6005Fuc` does pick H6001's colour sub-mode (0x0d when it holds, else
    0x02, on the h6001 automation and effect paths). It never applies to H6006 / H6008.
  - `secret` and the scene-dispatch sources: line numbers and field names updated to 7.7.02
    (`UrlConstants` p / y / f386y0, `Constant` e / f, `DefInfo` :106, `AbsPairAc4SecretV1` T1 /
    onEventSecretKey, `SecretKeyConfig` in base2home/scenes, `Diy.parseScenes4Diy` :95, `DiyGraffitiV3.a`
    :128). The matching §18.5 text in `GOVEE_BLE_GATT_PROTOCOL.md` is corrected too.
- `govee_reference.load_profiles` follows the app's family routing: cloud goodsType when non-zero, SKU only
  at goodsType 0. It used to map each SKU to the last family that listed it (H6102 -> dreamcolorlightv2).
  It now returns `{goodsType | goodsType-0 SKU: client_profile}`, rejects a key two families claim, and
  `profile_for(profiles, sku, goods_type)` does the lookup. The self-test checks the H6102 and H6001 routes.
- `devices.yaml` now records every light goodsType's colour-report strategy: how its Govee Home 7.7.02 control
  screen decodes the colour sub-modes 0x15 / 0x0d / 0x0b / 0x02 of a mode report it gets over BLE (the aa 05
  reply, or the 0xAC TLV 0x05).
  - How it was traced and checked:
    - Every goodsType and SKU-routed family was traced from its routing through to each colour decoder, with
      file:line citations.
    - Each trace was re-traced independently.
    - Spot-checkers re-traced over 330 samples from scratch across two rounds, and all of them matched.
    - The second round re-checked everything against the complete 7.7.02 decompile.
  - New top-level sections:
    - `mode_color_decode`: 19 body decodes. Each gives the byte layout, the Kelvin signedness, the rule for
      when the app treats the report as a colour temperature, and the matching ksy type.
    - `mode_color_parse`: 33 strategies, each a map from sub-mode to decode.
    - The library's strategy names are reused wherever the app's decode is exactly that strategy.
  - Per family: `client_profile.mode_color_parse`, with overrides `by_goods_type`, `by_model`,
    `mode_color_by_pact`, `mode_color_by_firmware` and `mode_color_parse_status`.
    - Coverage is 49 families and 113 goodsTypes.
    - h604a stays `untraced`: its feature split is not in the 7.7.02 capture.
  - `families[].assigned_goods_type` (18 items) records the goodsTypes the app assigns legacy SKUs itself, so
    a client can resolve their strategy:
    - BK-firmware rewrites to 39 / 40 / 17 / 44;
    - new-firmware rewrites to 17 / 18 / 19 / 20;
    - the two pairing binds, H613A-F to 39 and H615A-E to 40.
  - The section header records the app's routing rules:
    - family lookup by cloud goodsType, or by SKU at goodsType 0;
    - the device-list and scan-time pact checks;
    - when an entry is split by pact.
  - `devices.schema.json` gains every new property, strictly typed.
  - `mode_0d_kind` corrections:
    - tablelampv1 goodsType 128 (H6078) is `cct`, not the inherited `rgb`.
    - bulblightv3 goodsType 0 (old-firmware H6001) is `unmodelled`.
    - bulblightv3 goodsTypes 9 / 10 (IoT-only) and 311 (H601E/F, no 0x0d decode) are `none`.
  - Families gain the 46 goodsTypes and 43 models the app routes to them, each cited. H6184 leaves
    dreamcolorlightv2; it is chassislightv1.
  - Colour-report prose that the 7.7.02 code contradicts is corrected. Examples: s16be where the text said
    u16 BE / u2be; the right parser classes; source line numbers that ran past the end of their files.
  - `govee_ble.ksy` (doc text only): H61A8 (goodsType 19) reads the 0x15 Kelvin SIGNED. In 7.7.02 its screen
    is the rgbiclight `AcNewDetail`, whose `OpInfo4Detail.x` decodes 0x15 / 0x0b / 0x02 / 0x0d alike. The
    unsigned `SubModeColorV2.parse` serves only the legacy dreamcolorlightv1 screens, which goodsType 19
    never opens. This corrects the item-16 doc in the gap batch below.
  - `cct_read_reply`'s doc names the newdetail decoder and says the H6047 bar Kelvins are read signed.
  - Additivity: the readers are unchanged (harness: 2,006,142 cases, 0 differences). Both branches' suites
    pass (2037 each).
- Spec gap batch: the decodes the library still hand-codes, now in the spec so it can follow later. Each item
  was traced in the 7.7.02 decompile (firmware-sourced ones are marked with image and handler) and checked by
  an adversarial reviewer. Additive: no new enum is applied to a field, no `switch-on` gains a case, and no
  existing field, enum member or expression changes, except the two cases below. The additivity harness
  (1,975,202 cases against the 994ea64 readers) differs only in the 300 `mode_color_0d_typed(kind=3)` cases.
  Kind 3 used to read nothing and no caller passes it. The two intended `trailing` fields are excluded from the
  comparison. Both branches' suites pass with this spec (2037 each, the same as before).
  - New enums, applied to no field: `plug_command`, `light_power` / `relay_write` (the `33 01` value rules),
    `device_info_selector`, `status_tlv_type`, `rx_pro_type`, `encrypt_version`, `mode_0d_kind`,
    `op15_color_variant`, `mode_read_selector`, `multi_ac_command`, `multi_comm_byte` and `a3_start_marker`.
    Member names avoid the YAML 1.1 booleans (`light_on` / `light_off`).
  - New standalone types, wired into no dispatch:
    - `total_switch_write` (H5089 `33 02`).
    - `rx_frame`, a device-sent frame typed by proType that never raises on construction, with
      `multi_ac_reply_chunk` (the 0xAC chunk header, last_len checked as the app does) and `write_ack`.
    - `section_count_read` (`aa 41`, signed), `status_reply_safe` (an empty TLV buffer gives no entries) and
      `pact_read_reply` (`aa ef`).
    - `wifi_mac_read` (`aa 14`), `handshake_key_reply_data` (the E7 01 session key) and `bgc_info`.
    - `device_info_ble_mac` (`aa 07 06`) and `aging_flag_read` (`aa ff 08`), both firmware-sourced; the
      second is marked test-only.
    - The scene-param wrappers `scenes_graffiti_param`, `compose_scene_param`, `h60a6_scene_param` and
      `h6052_graffiti_param`.
    - In `govee_adv.ksy`: `scan_record` / `scan_ad_structure` (the app's scan-record AD walk) and
      `manufacturer_data_lenient`.
  - New instances on existing types:
    - `mode_read.scene_code`, and `cct_read_reply.kelvin_signed` / `kelvin_right_signed`.
    - `status_zone.zone_a_on` / `zone_b_on`, `bar_switch_read.left_on` / `right_on` and `gradual_read.is_on`.
    - `secret_read_reply.is_valid`, `sync_time_payload` / `plug_sync_time_payload.marker_is_valid` and
      `notify_wifi.is_connected`.
    - The uid / Wi-Fi-MAC validity tests on `device_info_basic` / `device_info_sn` / `device_info_wifi`.
    - `version3.text` with `is_zero` and the signed parts.
    - `manufacturer_data.is_govee_pact` / `has_switch_state` / `switch_byte`.
  - The two exceptions:
    - `mode_color_0d_report` and `bulb_group_color_read_v2` gain a `trailing` size-eos field. It holds the
      bytes after the decoded part and sits inside the body substream, so it never takes the BCC.
    - `mode_color_0d_typed` reads kind 3 as `[r, g, b][kelvin s16be]` from 5 bytes (h60a1 `Info4Detail`,
      cube `CubeNewInfo4Detail`).
  - User decisions applied:
    - `bgc_info` is lenient like today's library rule: it never raises, and `effective_encrypt_version`
      follows the library where the app throws (item 19).
    - `version3.text` matches the app's signed `x()` rendering, e.g. `81 05 F6` -> `-127.05.0-10` (item 21).
  - `devices.yaml` h5080 `client_profile.by_goods_type`, traced per goodsType: `relay_power`
    (`outlet0` / `all_outlets` / `total_switch`), `relay_on_mask` and `advert_on_mask` for 43, 50, 90 and 307.
    These come with the matching `devices.schema.json` properties.
  - Doc and citation fixes:
    - `device_info_read` / `read_request`: the H5086 `aa 07 10/11` senders are live, there are more
      `07 07` senders, and `device_info_selector` lists the `07 08` selector.
    - Corrected decode docs for `status_seg_info`, `bar_switch_write`, `op15_color_write` and the H60A6
      scene-upload rule.
    - Advert and GATT-doc line citations.
  - Catalogue: the `read_mode_bare` / `read_mode` docstrings, and their `OPCODE_COVERAGE.md` entries, cite
    the real senders (H6057 sends `aa 05 01`). The secret-key builder raises unless the secret is 0 or 8
    bytes.
  - Skipped:
    - 25-ext (per-goodsType 0x15 colour-strategy data in `devices.yaml`): the 0x15 signedness is recorded
      per SKU in `cct_read_reply.kelvin_signed`'s doc. A `by_goods_type` field needs each goodsType's detail
      screen traced first.
- Firmware-doc corrections. Every claim the spec makes about the six decompiled firmwares (H6047, H6006,
  H6008, H5083, H60A1, H60A6) was re-checked against the images by disassembly, and each correction had an
  adversarial check. Text and comments only: the ksy structure with docs removed, and its enums, are
  identical to before, so the generated readers and the wire behaviour don't change.
  - Corrected across `FIRMWARE_VALIDATION.md`, `GOVEE_BLE_GATT_PROTOCOL.md`, the ksy comments,
    `OPCODE_COVERAGE.md` and the catalogue docstrings:
    - H6047 is a BlueX/RivieraWaves ARM Thumb-2 SoC, not Telink.
    - Every Realtek image drops a bad-BCC frame in its shared write layer. The OTA heads `B0`–`B5`/`BF` skip
      that check on H60A1/H60A6 and are dropped on H6008.
    - A shared info layer answers `AA 06/07/14/20/21` (`EF` too, except on H6008) before the per-SKU
      dispatcher.
    - `AA 07` answers `02/03/06` only. H6008 and H60A1/H60A6 also take `0A/0B` with no data. `10/11` have no
      handler and get no reply, or an echo on H60A1/H60A6.
    - Byte 2 of an `AA 01/04/05` read is never read by the firmware. The reply bytes after the data are the
      request's bytes.
    - The bulbs accept `A1` only as `A1 11` (Wi-Fi).
    - `0x17` is a `33 FF` sub-type, not an opcode.
    - Head `0x66` is a test stub on the Realtek images.
    - The per-image opcode lists are corrected.
  - Cautions, marked firmware-sourced: `33 46` factory-resets and reboots H60A1/H60A6 whatever the payload
    (on the ksy `check_ic` enum and `build_check_ic` / `build_check_ic_step`). `33 FF`, `66 01/02` and
    `AA FF 08` are test-only.
  - `FIRMWARE_VALIDATION.md` §7 lists the firmware-sourced reads the app never sends, such as `AA 07 06`
    (BLE MAC), H60A6 `AA 30` / `AA 41` and `AA EF`, with the images and handler addresses for each.
  - `devices.yaml` h5080: the opcode set is labelled as the app's. The `0xA1` multi is the H5089
    night-light DIY write, not multi-outlet. A firmware note says H5083 SW1.00.30 dispatches no
    `0x16/0x1F/0xB4`.
- `spec/govee_reference.py`: `parse_frame`, `parse_status` and `parse_color_group` (and so `_selftest`) work
  with both reader builds. A read-write build (`tools/gen_kaitai.sh --read-write`) reads only on `_read()`, so
  those objects are read once; the read-only classes read on construction and are left alone.
- Standalone write-body types in `govee_ble.ksy`, so a client can serialize every library write with the Kaitai
  0.11 read-write classes (the header, zero padding and BCC stay in code). None is wired into a `switch-on`, so
  what the readers return is unchanged. Each layout and cap was traced in the 7.7.02 decompile and checked by
  an adversarial reviewer, and each round-trips the catalogue builders' frames through both the read-only and
  the read-write classes:
  - single-command bodies: `zone_power_write` (`33 30 [zone, state]`, 3 zones on H12D0),
    `light_direction_write` (`33 30 [value]`: H6038 direction, H6057 main-light switch), `gradual_write`
    (`[state]` under `33 a3` or `33 14`), `secret_key_write` (`33 b2`, 8 bytes, no selector) and
    `worry_free_at_night_write` (H6057's 7-byte `33 36` body). 0x30 and 0x36 are shared by several families,
    and `zone_power_write [1, 0]` is byte-identical to `light_direction_write [1]`, so the family decides.
  - op15: `op15_color_group_write` (`[group][RGB x 1..4]`, 1-based groups) and one `op15_color_write(variant,
    mask_width)` covering basic (variant 1), H60A1/H60A6 RGB (12), CCT (11) and colour-instruction version 5,
    with the mask width checked against each form's app cap (12 / 7 / 7 / 10 bytes: 17 minus the ext bytes
    before the mask).
  - wide masks: `op15_brightness_wide` (cap 14), `color_rgbic_0b_wide` (cap 13), and `color_0b_kelvin` (the
    fixed 7-byte colour-version-6 0x0b form, which the existing 0x0b case can't tell apart).
  - `read_request`: a writable `0xAA` request, `[command][selector]`, with every selector the app's
    controllers define. `aa 01` / `aa 04` are bare, as the app sends them.
- Per-family power and H60A6's 0x0D report, traced in the 7.7.02 decompile with an adversarial check per
  family:
  - Every curated control screen that reads the aa 01 reply decodes it `== 1` (`ControllerSwitch`). No
    curated or profiled control screen reaches the legacy `SwitchController` `!= 0`, though older detail
    screens for legacy goodsTypes (e.g. dreamcolorlightv1 `adjust/v1`-`v2`) and scene / IoT command paths do. The two rules are now
    instances: `switch_read_reply.light_is_on` (`== 1`) and `status_switch.is_on` (0xAC TLV 0x01, `!= 0`).
    `devices.yaml` `client_profile` gains `power_read` (`aa01` or `ac_status`), `power_live`
    (`heartbeat_aa01`, `heartbeat_aa36`, `push_30`) and a `by_goods_type` override map
    (`devices.schema.json`), filled for the 9 curated families. H60A4/H60A6 and H6039/H6038/H6048 send no
    aa 01 read and take connect-time power from the 0xAC status TLV 0x01. h60ax is recorded per goodsType,
    with its six untraced goodsTypes left out.
  - H60A1/H60A4/H60A6 decode the 0x0D report as `rgb_kelvin_0d` on every pact (h60a1 `Info4Detail.Q`:
    `[R,G,B,kelvin s16be]`, CCT only for a signed kelvin > 0, tint never read); new
    `mode_color_0d_typed.is_cct_positive`. 0x15 stays H60A6's main colour report on pacts (1,2)/(1,3).
- op15 segment masks in the reference and catalogue (the ksy `mask_width` param waits for a change made
  together with the library): `bleutils_x`, `make_selected_two_bytes`, `op15_mask` and `op15_mask_width`
  mirror `BleUtils.X` / `makeSelectedTwoBytes` (`ceil(n/8)` bytes above 16 segments, LSB first);
  `build_op15_color(segments=...)` writes the wide mask (the default keeps the 2-byte form); new
  `build_op15_color_bytes_h` / `build_op15_color_temp_bytes_r` mirror `Bytes.h` / `Bytes.r` (7-byte cap, as
  the app has); `build_op15_gradient_color` raises above 48 segments, where the app's `Bytes.n` throws,
  instead of silently returning the 48-segment frame; `append_bcc` raises on a payload over 19 bytes. All
  match the library's `wire.build` byte for byte over 3-56 segments. 760 builder self-checks.
- Spec-client audit fixes, each verified against the 7.7.02 decompile before applying:
  - Signed 16-bit fields: the app reads the 0x0D mode-report kelvin (kinds 0 and 2) and the `aa 40` IC
    count with `BleUtil.getSignedShort`. `govee_ble.ksy` keeps the raw `u2be` fields for existing readers
    and adds `kelvin_signed`, `report_kelvin_signed` and `ic_count_signed` instances with the app's
    reading; the `devices.schema.json` `mode_0d_kind` text no longer contradicts itself (`> 0` vs `!= 0`).
  - 0xAC status TLVs: `status_tlv.len` gets `valid: {max: 0x7f}` (the app reads it signed and fails from
    0x80), and a standalone `status_device_info` type records that the status parser decodes only selectors
    0x10 / 0x11 of a 0x07 block and ignores the rest (the 0x07 case still decodes as `device_info_read`
    for existing readers).
  - `devices.yaml` / GATT doc: BGC-info byte 0 is the record format and byte 1 the encryptVersion (the
    prose had them swapped); `GVR6184_XXXX` parses to `R6184`, not `H6184`; and the name-length gates
    (≥ 11 for GVH/GVR without `_`, ≥ 10 for the bare `GV` fallback) are documented.
  - `govee_commands.build_brightness_from_percent` computes in float32 like the app's `100.0f` (446
    off-by-one results change, none in the app's real raw ranges). Reader regenerated (additive: the three
    instances, `StatusDeviceInfo`, and the `len` check).
- `tests/test_wire_build_vs_spec.py` (new): every TX builder in `wire.build` and `transport.handshake` is
  compared byte for byte with its counterpart in the spec's app-traced catalogue (`spec/govee_commands.py`)
  or reference codec (`spec/govee_reference.py`) over seeded-random inputs: power, brightness, every colour
  scheme (the tint-pick-as-CCT path over all 178 tint colours), segments up to the 7-byte mask, zones,
  bars, every scene code, 0xA3 and 0xA4 uploads, plug and secret writes, every read request and the `e7`
  handshake. The 0x15 colour and CCT frames are bound at every segment count up to 56 to the catalogue's
  `Bytes.h` / `Bytes.r` builders (the wide mask, added to the spec in faa2cb7). Every builder now equals its
  catalogue counterpart; a frame that has to differ pending a decision goes in a ratchet that asserts the
  difference instead of being skipped (the `aa 01` / `aa 04` selector was the last; see Changed).
- The protocol constants in `wire.build` (`PRO_*`, `CMD_*`, `SUB_*`, `OP15_*`) and `transport.handshake`
  (`HANDSHAKE_OPCODE` and the e7 steps) are now taken from the generated reader's enums instead of being
  copied by hand; the names and values are unchanged (pinned by the test above). The device-info selectors
  (`INFO_*`) and power values (`POWER_*`, `RELAY_*`) stay literals: the ksy has no enum for them yet.
  `wire.build` is now the wire layer's one import of the generated reader.
- More decoders read through the generated readers instead of slicing bytes: `parse_direct_color_group` and
  the 0xAC colour groups (`color_group_read`); `parse_mode_0d` (`mode_color_0d_typed` kind 2, now the same
  decode as `parse_mode_0d_rgblight`; it no longer takes the 8-10-byte frames the transport never delivers);
  the `e7` handshake replies (`handshake`); `identify.parse_manufacturer_data` and `parse_broadcast_onoff`
  (`govee_adv` `manufacturer_data`; a company id outside 16 bits, which bleak never reports, is skipped); and
  the fallback 0xAC TLV walk (`status_tlv`, read off one shared stream). `tests/test_spec_reader_moves.py`
  pins each to the byte-level decode it replaced; offline they were identical on 1.8M advertisement, 450k
  handshake, 450k colour-group and 200k TLV-walk cases. The `wire.parse` module docstring now names the two
  replies still hand-decoded (the `aa 05 04` scene code and the `aa 41` section count) and why. No frame
  changed.
- Re-verified several spec facts against the 7.7.02 decompile (adversarially) and the account device-list,
  and corrected them: `switch_read_reply` keeps the legacy `SwitchController` `!= 0` semantics as primary and
  now notes the modern control-screen VM path (`ControllerSwitch.Companion.c` / `ShareVM.N3`) that decodes
  `== 1` (they differ only for a byte ≥ 2); `MtuConfig` is described as a per-SKU **cache of the negotiated
  ATT MTU** (default 23, usable size = MTU − 3 floored at 20), written by `saveMtuData` from `onMtuChanged`
  on a successful exchange — not an app value unrelated to negotiation. Updated `govee_ble.ksy`, the GATT doc
  and `SCENE_UPLOAD_ENCODING.md`; reader regenerated (one docstring).
- `spec/devices.yaml` goodsType annotations corrected from the account device-list: H6006 and H6008 are both
  goodsType 64 (was `H6006 = 20` / `328 = H6008`; 328 is H8015, 20 is the H6001 legacy old-bulb), and H61A8
  is goodsType 19 (was `207 = H61A8`; 207's SKU is cloud-only and is not H61A8). H6006's colour-temp write
  uses the 0x0D native path like H6008, not 0x02. goodsType 19 added to the `dreamcolorlightv1` set.
- `spec/govee_commands.py`: made ruff-clean and `mypy --strict`-clean under the repo config (style and
  typing only — annotations don't execute under `from __future__ import annotations`; the builders and the
  757 self-checks are unchanged).
- `spec/govee_commands.py` (new): the full command-builder catalogue — 463 functions, one per
  (opcode, variant) the Govee app emits across 99 opcodes, each traced to the decompiled app with a
  `file:line` citation and a byte-exact self-check. `govee_reference._selftest` runs all 757 checks.
  Companion to `govee_reference.py` (which keeps the framing primitives). Device-side cross-validated
  against six firmware images; see `docs/FIRMWARE_VALIDATION.md`.
- `spec/docs/OPCODE_COVERAGE.md` (new): opcode → builders index, with each opcode's verification tier
  (firmware-confirmed vs source-modeled), whether `govee_ble.ksy` models it, the constant-only opcodes,
  and the known gaps.
- `spec/docs/DEVICE_CATALOGUE.md` (new): the app's full goodsType universe (390 values) vs the 79 the
  spec models across 51 families, with the unmodeled lighting/plug families and the out-of-scope set,
  from a source-grounded, adversarially-verified cross-check of the app's `GoodsType`/`Pact` registry.
- `spec/docs/FIRMWARE_VALIDATION.md` (new): the device-firmware cross-validation of the frame model,
  checksum and opcode inventory (six images: H6047 Telink + five RTL8720 Realtek), with the resolved
  discrepancies (handshake is `0xE7`; `0xA5` is the app's mic framing; `0x66` is device-only).
- `govee_ble.ksy`: `command` enum `0x25 preset_color` and `0xab dynamic_api_support` (were in the MD /
  registry, missing from the enum); `pro_type 0xa5` + `mic_color_frame` (mic/music live-colour push — a
  short, additive-checksum frame, deliberately NOT a `frame` case); standalone `bar_switch_write`
  (`33 36` body `[left,right]`, not wired into `single_command` so a 0x36 frame's `params` stays raw);
  `a3_start.comm_byte` doc gains V5=10 (compose) / V6=12 (H610B, shared with V7). Reader regenerated.
- `spec/devices.yaml` / `devices.schema.json`: `mode_0d_kind` gains `rgb_kelvin_0d` (cube h6061 0x0d =
  `[R,G,B,kelvin s16be]`, no tint); the `rgb_kelvin_tint` and h6188 `mode_read_color` notes corrected to
  signed Kelvin (`getSignedShort`); the h60a6 `0xA3` END documented as empty for a short value
  (`makeSendBytesV2`). `GOVEE_BLE_GATT_PROTOCOL.md` §17.3 ABNF `com-type` gains `%x04` (V4), `%x0A` (V5).
- `govee_ble.ksy`: `soft_version_read` (`aa 06`, ASCII) and `device_info_hard_version` (`aa 07 03`,
  ASCII). Readers regenerated: these additions plus docstrings only (parsing unchanged).
- 0xAC status reply (GATT §4.5, the §14 validation table and the §17.3 ABNF; `govee_ble.ksy` `multi_ac` /
  `status_reply` docs), per `AbsController4BleSingleSendMultiBack`: bytes 2-3 of the first chunk are the
  total packet count (u16 LE) and byte 4 the last chunk's data length; the 0xFF chunk is the last data
  packet, not an empty terminator, and the app also accepts it tagged with its position (pkts - 1).
  Single-packet replies are documented, and the padding note says the shipped reassembler trims to lastLen,
  like the app.
- `spec/govee_reference.py`: `reassemble_ac` / `chunk_ac` follow the app (packet count, lastLen, a last
  packet tagged 0xFF or its position, 0xFF refused mid-burst) and reproduce both real H60A6 bursts byte for
  byte (`tests/test_spec_reference_crosscheck.py`); `chunk_ac`'s `sub` defaults to 0x00 (was 0x02), the
  H60A6 status read's `d()`. The self-test falls back to the library's generated classes (after
  `tools/gen_kaitai.sh` or `pip install -e .`), so a run from a checkout executes every parser check, and
  `--strict` fails if they are skipped.
- `govee_adv.ksy`: `parseBleBroadOnOff` reads `data[6]` (`scanRecord[i6+8]`, the first byte after
  pact_code; `BleUtil.java:749`), not `data[3]`.
- 0xA3 upload (`govee_ble.ksy` `a3_start.packet_count`, GATT §4.4, SCENE_UPLOAD_ENCODING.md G7): a value that
  fits START's 15 - len(commBytes) inline bytes gives packet count 2 and an empty 0xFF END
  (`MultipleControllerCommV1.makeSendBytesV2`); a longer value's END carries the final chunk, a full 17 bytes
  when it ends on a frame boundary. The H6006 firmware's a3 reassembler (`FUN_9b020d2c`) accepts both (static
  firmware analysis). Live-verified on an H6006 (2026-10-15, macOS), which acked a short upload (packet count
  2, empty END) and then reported the scene active.
- SCENE_UPLOAD_ENCODING.md §0: commByte is the controller comType (V1=1, V2=2, V3=7, V5=10 compose,
  V6=12 H610B), and compose is uploaded only when the decoded blob starts with 0x0a (`ScenesOp.o`).
- `mode_color_0d_typed` kind 2 (`rgb_kelvin_tint`) covers the 0x0d bulbs H6006/H6008 as well as H6188
  (bulblight `ModeParseStrategyConfig.a`, traced through `VM4Light` to `OpInfo4Detail.parseModeValidBytes`,
  which takes CCT only for a signed Kelvin > 0); kind 1 stays H6052, whose control screen reads only
  `[R,G,B]` (`H6052InfoDetail.G`). The H6006/H6008 firmware sends the tint as 0 and pre-zeroes the Kelvin
  (static firmware analysis). `mode_read`, `mode_color_0d_report` and `is_cct` docs match; `devices.yaml`
  bulblightv3 `mode_0d_kind` is `rgb_kelvin_tint` (was `cct`), and the schema and USING_THE_KSY.md follow.
- `devices.yaml` h5080 `relay_switch`: the read-back on-rule per goodsType (43 = bit 0; 50/307 = any of the
  8 bits; 90 = bits 0..2), matching the three plug profiles' `relay_on_mask` 0x01 / 0xFF / 0x07.

## [1.3.0] — 2026-10-15

### Added
- **H6004** (BLE bulb, `bulblightv3` split, goodsType 64; #14): a profile-only addition that behaves
  like its H6008 sibling: power, percent brightness, RGB, colour temperature (2700-6500 K, the app's
  both-slot 0x0d frame with the Kelvin tint, 100 K steps), the cloud scene library (63 scenes,
  type-1 RGB upload; `scene_versions={0, 1}` per `ScenesDelegateRegister` / `Support4BulbLightV3.g`)
  and polled read-back. Not implemented yet: DIY, the phone-microphone music mode and the timers
  (4 timers, wake-up, sleep). Plaintext, no account lock. Modeled from the decompiled
  Govee Home 7.7.02 app (`Config.x[64]` -> `Config.bu` -> `Bytes.p`, `KelvinConfig` map 64 -> V2,
  `bulblightv3/pact/Support`); not hardware-verified. H6004 has a second, Wi-Fi-only hardware
  revision (goodsType 10) with no BLE path; the library can only drive the goodsType-64 one.
  `tools/field_test/plans/h6004.py` adds its field-test plan, and `scenes/H6004.json` is the bundled
  catalog (`tools/fetch_scenes.py`).
- **H6188** (legacy RGB strip, `rgblight` split): power, brightness, RGB, colour temperature
  (2000-9000 K), the app's nine built-in scenes, and polled read-back. Modeled from the decompiled
  Govee Home 7.7.02 app; not hardware-verified. Its siblings H6085/H6086/H6089 are Wi-Fi-only
  (soft-AP pairing, no BLE path in the app) and stay unsupported.
- Colour scheme `"h6188"`: same 0x0d RGB frame as `"h6006"`, but the CCT frame carries the app's
  Kelvin->RGB tint bytes (`Config.GM` -> `Bytes.A`); new `wire/cct.py` holds the tint table,
  generated from `Constant`'s colour->Kelvin map.
- `DeviceProfile.brightness_range`: raw (min, max) brightness for devices whose brightness byte is
  not a percent (H6188 = 20..254, `BrightnessConfig` version 0). Writes map 1..100 with
  `NumberUtil.calculateProgress`, reads with `calculatePercent` (Java `float` math, exact round trip).
  New builder `build.brightness_raw`.
- `parse.parse_mode_0d_rgblight`: the H6188 `aa 05 0d` report, decoded through the generated
  `ModeColor0dTyped(kind=2)` reader; any non-zero Kelvin means colour-temperature mode.
- Bundled `scenes/H6188.json`: the fixed `Scenes4H6188` list (activate-only, `33 05 04 <code>`).
  `tools/fetch_scenes.py` skips app-defined catalogs (`APP_DEFINED_SCENE_SKUS`) so a fetch can't
  overwrite them with the cloud library.
- `tools/field_test/`: guided hardware test for someone who owns the device (scan, every capability
  with read-back and a visual check, restore, JSON report + frame capture to send back). One
  runner with a test plan per SKU: the SKU is detected from the scanned device (or `--sku`), and each
  device adds `plans/<sku>.py` calling `register_plan(SkuPlan(...))` without touching the runner.
  Ships with the H6188 plan. Bluetooth addresses, serials and Wi-Fi MACs stay masked in both
  output files (see #17).
- `Device.segment_count` and `Device.set_segment_count()`: a segment count learned at runtime
  overrides the profile's static `segments` for whole-device masks, segment indices and the
  `aa a5` read-back walk (a profile's explicit `color_readback_segments` still wins for the walk).
  `None` reverts to the profile value. H6062 sets it from its `aa 41` read; for H6061 the caller sets it from a known layout.
- **H6062** (Glide wall light, `striplight` split, goodsType 41; #15): power, brightness (percent),
  RGB, colour temperature (2000-9000 K, with the app's tint), per-segment colour / brightness /
  colour temperature, polled read-back with per-segment colour, and the cloud-library scenes
  (dialect A, versions {0,1,2,3}; bundled `scenes/H6062.json`, fetched with `tools/fetch_scenes.py`;
  `ScenesOp.parseSceneV1`, activate `33 05 04 <code u2le>`, independent of the segment count). Frames
  are the `h60a6` 0x15 layout.
  Modeled from the decompiled Govee Home 7.7.02 app; not hardware-verified.
  - The device reports its own segment count with `aa 41` (`[IC s16be, sectionNum u8]`,
    `ControllerIcSegmentNum`, a mandatory step of the app's connect sequence:
    `AbsComposeControllerWithOpResult.onOvertime` fails the whole info read on a timeout). The
    library reads it on every poll (between `aa 04` and `aa 05`, the app's order) and before the
    first write that carries a segment mask, and uses `sectionNum` as the segment count: the mask
    width (2 bytes up to 16 segments, 3 for 17-24, ... 5 for 33), the segment index bound and the
    `aa a5` group walk of ceil(n/4) groups. If the device does not answer, no colour frame is sent
    (the app cannot control the device without the count), rather than guessing a mask.
  - New `DeviceProfile.section_count_read`, `build.section_count_query`, `parse.parse_section_count`.
    `profile.segments` (33) is only the upper bound (`Support.getBulbStringMaxNum`: 19 for pacts
    (1,x)/(2,x), 28 for (3,1), 33 for (4,1)).
  - `tools/field_test/plans/h6062.py` field-test plan.

### Changed
- **Out-of-range Kelvin is clamped to the device's range instead of raising `GoveeBleInvalidValue`**
  (`set_color_temp`, `set_segment_color_temp`, `set_zone_color_temp`, `turn_on(kelvin=)`), matching the
  app: `KelvinConfig.Companion.a` (`calibrationKelvin`) and `Constant.checkKelvinRange` clamp any int to the
  per-SKU range and never reject it. Order is clamp, then round to 100 K, then clamp again;
  `state.color_temp_kelvin` stores the value actually sent. Only a non-int (or, for a profile with no
  known range, a value that does not fit the wire's u16) still raises. `build.normalize_kelvin` now
  raises only for those cases instead of for any value outside 0..65535.
- New `Device.pact` property (`(pact_type, pact_code)` from the advertisement, or `None`) and
  `build.normalize_kelvin`.
- `tools/field_test/plans/h6188.py` drops the 2750 K "no tint" diagnostic step: 2750 K is now rounded to
  2800 K like the app, so the step no longer tests anything.
- Segment masks are no longer capped at 16 bits. `build.mask_width(segments)` follows the app's
  encoder (`BleUtils.makeSelectedTwoBytes` / `BleUtils.X`, picked by the `length > 16` branch in
  `color/Bytes`): up to 16 segments a 2-byte little-endian mask (unchanged), above that
  ceil(n/8) bytes, LSB first (byte j bit i = segment 8j+i). More than 7 bytes (56 segments, the
  most `Bytes.C` encodes) raises `GoveeBleInvalidValue`. `all_segments_mask` is now exact instead
  of silently clamping to 16 segments, and raises above 56. `segment_rgb`, `segment_color_temp` and
  `segment_brightness` take an optional `segments` count that sets the mask width; omitted, they
  keep the two-byte mask. `color_rgb` / `color_temp` use the width for their `segments` argument.
  The 0x0b (`h61a8`) frame keeps its fixed two-byte mask. No shipped device changes.
- Segment indices on a device with more than 16 segments run 0..n-1; up to 16 segments the
  0..15 range is unchanged.
- **H6047 gradual** (#13): `gradual=True` in the profile. `update()` reads `aa a3` and
  `set_gradual()` writes `33 a3 <on>`; the app reads it on every connect (`ControllerGradual4BleWifi`) and
  its colour screen writes it (`ColorModeControllerConfig.p` -> `Config.G[119]` -> `Bytes.w`).
- **H6047 reports a kelvin per bar** (#13). `aa 05 15` is `[gradual][kelvin left u2be][kelvin right u2be]`
  (`SubModeColor.parse`, `Info4Detail` parse strategy); new `parse.parse_bar_kelvin`,
  `DeviceState.zone_color_temp_kelvin` (keyed by zone `power_index`, like `zone_power`) and
  `Device.zone_color_temp_kelvin(name)`. `DeviceState.color_temp_kelvin` is the whole-device value and is
  set only while both bars agree, as in the app; a colour-mode reply clears both. Writes
  (`set_color_temp`, `set_zone_color_temp`, `set_rgb`, `set_zone_rgb`, `set_scene`) keep these in step
  until the next read.
- `tools/field_test/plans/h6047.py`: the H6047 field-test plan (whole and per-bar power, brightness,
  RGB, whole and per-bar CCT, gradual, 10-segment read-back; restores the bars, colours and gradual flag).

### Fixed
- **H60A6 whole-device colour temperature is one frame, like the app's Kelvin sliders.** The library sent
  two frames (main panel, mask `00 10`, then ring, mask `ff 0f`) whenever the advertised pact was
  (1, >= 3) or unknown, copied from `ControllerBuild.j`, which is only the colour-stripe/snapshot apply
  path (`VM4LightH60A6.configColorStripController`). A 7.7.02 trace of a plain whole-device Kelvin set
  shows one frame: the colour tab (All mode) is `DelegateColorV2.G0` (posSet = 13 trues,
  `ColorModeUtil.e`) -> `ColorViewModel.U` -> `ColorModeControllerConfig.c` -> `Config.Yt` ->
  `Bytes.r(tint, K, posSet, 1)`, and the illumination tab is `KelvinConfig.b` ->
  `ColorModeControllerConfig.h` -> `Config.N[0]` -> `Bytes.C(K, 13, 1)`. At 4000 K both give
  `33 05 15 01 ff ff ff 0f a0 ff d5 a1 ff 1f`. `set_color_temp` and `turn_on(kelvin=)` now send that one
  all-segments frame; `set_zone_color_temp` keeps its per-zone frames. `DeviceProfile.cct_per_zone_min_pact`
  (added unreleased) is removed with the two-frame gate. The app's pact gates are not modelled beyond this:
  below pact code 2 it sends the 0x0d form (`Config.kw`, `Bytes.p`), which the library does not, and it
  sends the 0x15 frame for an unknown pact like the library does.
  Live-checked on an H60A6 (pact 1/3): the single frame is acked and the read-back of the main panel
  and all 12 ring segments equals the tint table at 4000 K (`ff d5 a1`) and 2700 K (`ff ae 54`).
- **An RGB pick equal to a Kelvin tint colour is sent as CCT on every device, like the app.** The
  H6061-only rule now covers every colour scheme with a CCT mode. When the picked colour's full ARGB int
  is exactly one of the 178 colours of the app's Kelvin tint table (`Constant.k2` = `ColorUtils.e`, two
  per 100 K step) and its Kelvin is inside the SKU's inclusive range (`KelvinConfig.i` / `.c`), 7.7.02
  writes a CCT frame instead of the RGB one, and the library did not, so such a pick lit the device with
  a slightly different colour than the app (and `state` reported RGB instead of the Kelvin the device
  reads back). 0x15 family (H60A6 2700-6500 K, H6641 and H6062 2000-9000 K, H6047 2200-6500 K):
  `ColorModeControllerConfig.c` -> `Config.Yt` = `KelvinConfig.i(c) ? Bytes.r(c, KelvinConfig.c(c), ...)
  : Bytes.h(c, ...)`, i.e. `33 05 15 01 ff ff ff <K> <picked RGB> <mask>`: one frame, the picked colour as
  the tint (not the first tint of the Kelvin), the mask the RGB frame would use, for `set_rgb`, `turn_on(rgb=)`, `set_zone_rgb` and `set_segment_rgb`
  (H6047 reaches it via `NewDetailVm.configColorController` -> `H6047ColorControllerConfig.a` -> `Yt`).
  0x0d family (H6188 2000-9000 K, H6008/H6004 2700-6500 K, H6052/H6006 2000-9000 K): `Config.bu` =
  `KelvinConfig.i(c) ? Bytes.p(c, KelvinConfig.c(c)) : Bytes.f(c)`, i.e. `33 05 0d <picked RGB> <K>
  <first tint of K>` for both the `h6006` and `h6188` schemes (not the white-slot `Bytes.A` form, which
  stays the H6188 slider). Afterwards `color_temp_kelvin` is the Kelvin and `rgb_color` is `None` (both
  bars on H6047). One mechanism for all schemes: `build.rgb_as_kelvin` takes the range from the profile;
  `segment_color_temp` takes an optional `tint`; `set_color_temp` is unchanged. Not modelled: colour-set /
  mixed-colour picks and the real-time drag frames (`a5 02 83`, RGB only); H61A8 is not determined.
- **CCT frames now send the Kelvin tint the Govee app sends** (#19), which the library left as zeros
  everywhere except H6188. Following the app's own colour-control path for each SKU:
  - **H6047, H6641, H60A6** (0x15 white-slot form `15 01 ff ff ff <K> <tint> <mask>`, `Bytes.r` type 1;
    `ControllerBuild.d`, `Config.Yt`): `color_temp`, `segment_color_temp`, `set_zone_color_temp`
    always fill the tint.
  - **H60A6 whole-device CCT is one all-segments frame** (see the separate H60A6 entry above); per-zone
    CCT is one frame per zone (main panel mask bit 12, ring bits 0-11).
  - **H6008, H6006, H6052**: new 0x0d both-slot form `0d <tint> <K u2be> <tint>` (`Config.bu` ->
    `Bytes.p`), replacing `0d ff ff ff <K> 00 00 00`. H6188's `0d ff ff ff <K> <tint>` form is unchanged.
  - Kelvin is clamped to the device range and rounded to the nearest 100 K before sending
    (`KelvinConfig.calibrationKelvin`; the app's slider only produces 100 K steps, and its builders
    recover K from the tint table). `set_color_temp(2750)` therefore sends 2800 K and reports 2800 K in
    `state`.
- `parse.parse_mode_0d` (the 0x0d bulb/lamp read-back) now treats any non-zero Kelvin as CCT, as the
  app's parsers do (`ModeParseStrategyConfig`, `SubModeColor.parse`), instead of requiring `ff ff ff`
  in the colour slot; the app's own CCT write puts the tint there, so the old rule read it back as RGB.
- **H6047 reported two phantom segments, 10 and 11** (#13). Its goodsType 119 uses the old detail screen
  (`h6047/pact/Model.t` -> `NewDetailAc`), whose read-back walks `Support.getBulbStringMaxNum(119)` = 10
  pieces in groups of 4 (`NewDetailVm.n5`: `aa a5 01..03`, `SubMode4Color` trims the last group to 2
  records). The profile used `getColorPieceSize` = 12, which only the newdetail screens (277/288/298)
  call, so the last group kept 4 records. `color_readback_segments` is dropped: read-back is the same 10
  segments as the write.
- **H6047 whole-device power is `33 01 <on>`**, as the app's main switch sends it
  (`NewDetailVm.switchTrigger` -> `ControllerSwitch`). The library sent `33 36 <on> <on>`, which the app
  only uses for the per-bar buttons; `set_zone_power` still sends `33 36`. Powering on leaves the per-bar
  state unknown until the next `aa 36` read (the app does not say which bars `33 01` lights), so the
  next zone change re-reads the bars instead of assuming; powering off marks both bars off. `aa 36`
  read-back now sets `is_on` to "either bar on", as the app's heartbeat does.
  The `aa 01` poll stays: the app's connect read (`Compose4InfoBleIot`) includes `ControllerSwitch`, and a
  timeout there fails its whole connect, so an H6047 answers it (a miss would cost one 2 s read timeout and
  is non-fatal; `aa 36` still supplies power).
- **H6061** (Glide Hexa light panels, `cubelight` split, goodsType 51), phase 1 = whole-device control
  only ([#16](https://github.com/Brady-Woods/govee-ble-local/issues/16), tracked in #17): power,
  percent brightness, RGB, colour temperature (2000-9000 K) and polled read-back. Modeled from the
  decompiled Govee Home 7.7.02 app; not hardware-verified. No SEGMENTS or SCENES capability yet, and
  no secret (the app writes 0xB2 only while pairing). New colour scheme `"h6061"`:
  - RGB is `ceil(n/4)` frames `33 05 15 04 <group from 1> R G B x<=4`, the last carrying the
    remaining panels (`Bytes.m`, `BleUtils.w0`; `ColorModeControllerConfig.k` -> `w` -> `l`); with
    the default 23 panels that is 6 frames, the last with 3 colours. New `build.color_rgb_frames`;
    `Device.set_rgb` / `turn_on(rgb=)` send the frames with `send_many`.
  - CCT is `33 05 15 01 <tint RGB> <kelvin u2be> <24-bit mask>`, the first n mask bits set, LSB first
    (`ff ff 7f` for 23 panels; `Config.Zt` -> `Bytes.u`, `ColorPieceConfig.k`). The tint sits in the
    colour slot, not the `ff ff ff` white-slot form of H60A6 / H6047 / H6641.
  - An RGB pick that is exactly one of the app's 178 Kelvin tint colours (two per 100 K step, within
    2000-9000 K) is sent as the CCT frame, with the picked colour as the tint, as the app does
    (`ColorModeControllerConfig.k`: `ColorUtils.q` + `KelvinConfig.i`, then `Config.Zt`); the state
    then reports that Kelvin. New `wire.cct.kelvin_for_tint` and `build.rgb_as_kelvin`.
  - Panel count n defaults to 23, the app's value when no layout is stored (`ColorPieceConfig.f`,
    `Support.getBulbStringMaxNum`). `Device.set_segment_count(n)` sets it for a known layout
    (1-23; more is refused because the app sends no colour for a longer set).
  - Read-back polls `aa 01` / `aa 04` / `aa 05`: the colour comes back as mode `0x0d`
    `[R, G, B, kelvin u2be]` (kelvin > 0 = CCT) although writes use `0x15`
    (`CubeNewInfo4Detail`), then `aa a5 <group>` for groups 1..`ceil(n/4)` fills `state.segments`
    (`CubeVM4Light.l5`).
  - `tools/field_test/plans/h6061.py`: power, brightness, RGB (6 group frames) and CCT at
    2000 / 4000 / 6500 / 9000 K.

### Spec
- `devices.yaml`: `color_temp_write` opcode for `h60ax`, `h6047`, `h61d3`, `bulblightv3` and
  `tablelampv1`, with the decompiled sources. `govee_ble.ksy` (+ generated docstring): `color_cct_0d`
  documents both CCT forms.
- `govee_ble.ksy`: `mode_color_0d_typed` kind 2 (rgblight `[R,G,B, kelvin u2be, tint RGB]` with an
  `is_cct` instance); `color_cct_0d` / `mode_color_0d_report` docs cover the H6188 forms. Readers
  regenerated (kaitai-struct-compiler 0.11.0; the diff is only these additions).
- `devices.yaml`: new `h6188` family with citations; note on the Wi-Fi-only H6085/H6086/H6089.
  `devices.schema.json`: `mode_0d_kind` gains `rgb_kelvin_tint`.
- `devices.yaml`: new `striplight` family (H6062, goodsType 41; the shared package's H610A/H610B have
  their own flows and are not modelled). `govee_ble.ksy`: comment that opcode `0x41` is
  `on_off_memory` in the shared enum but the H6062's `aa 41` section-count read on the striplight
  split (hand-decoded in `parse.parse_section_count`; generated readers unchanged).
- `devices.yaml`: the `bulblightv3` family now records H6004's two hardware revisions: goodsType 10
  (`bulblightstringv1/bulblightv1`, Wi-Fi/IoT-only, `IotSkuItemV2.needBle()` false) versus goodsType
  64 (BLE + Wi-Fi), with the citations; H6003 is goodsType 9 (IoT-only).
- `devices.yaml` `h6047` family re-audited against 7.7.02 (#13): kelvin per SKU (H6043/H6042 =
  2000-9000 K, only H6047 is 2200-6500 K); read-back count depends on the screen (old detail screen
  `getBulbStringMaxNum`: H6047 = 10, H6042 = 5; newdetail screens `getColorPieceSize`), so the
  "read 12 != write 10" claim is gone and `getGoodsType4ColorSegment` is the only one that never returns
  5; whole-device power `33 01` vs per-bar `33 36`; `aa 36` third byte (movie/feast); `aa 05 15` and
  `a3` documented; swap light `0x34` and the connect-time `aa ac` sound-pickup read documented, not
  modelled; left/right order is logical (`LightPosAc`); "observed plaintext" no longer claimed for H6047;
  line citations updated (`Support.isGoodsTypeH6047` is at :186). `govee_ble.ksy`: `cct_read_reply` gains
  `kelvin_right` and the gradual-flag meaning of byte 0, `bar_switch_read` gains `movie_feast`. Readers
  regenerated (the diff is only these two additions).
- `devices.yaml`: new `h6061` family (Glide Hexa, phase 1) with citations: the group RGB frame, the
  tint-in-colour-slot CCT frame with its 24-bit mask, the `0x0d` colour report and the `aa a5` reads.

## [1.2.1] — 2026-10-13

### Fixed
- Installs alongside current Home Assistant again ([#8](https://github.com/Brady-Woods/govee-ble-local/issues/8)): the `cryptography<49` ceiling conflicted
  with HA 2026.10's `cryptography==50.0.1`, so the integration failed to load. The requirement is
  now `cryptography>=41.0.0` with no upper bound: HA installs integration requirements under its
  own `package_constraints.txt`, so inside HA the version always follows HA's pin, and a ceiling
  could only break again when HA moves. The `pip-audit` ignores for PYSEC-2026-3552/3553/3554 are
  gone with it (fixed in the `cryptography` versions now resolved).
- BLE name -> SKU extraction now matches the app (`BaseBleProcessor.parse`). The bare `GV` form
  returned the wrong SKU (`GV61881A2B` -> `61881`, `GV6188_1A2B` -> `6188_`; the app reads
  `"H" + name[2:6]` -> `H6188`), so `discover()`/`match()` could never match such a device.
  Also aligned with the app: `GVR` keeps its `R` (`GVR6184_XXXX` -> `R6184`); `GVH`/`GVR` names
  without `_` need >= 11 characters and `GV` names >= 10 (shorter names are no longer guessed);
  `ihoment_`/`Govee_`/`Minger_` are matched anywhere in the name, not only as a prefix.
  Verified against live advertisements (H60A6 and H6008 in the `GVH…` form; H5083, H6047, H6052
  in the brand form): same SKUs as 1.2.0, and no non-Govee device newly matched.

## [1.2.0] — 2026-10-12

First release published to PyPI. `1.1.0` was bumped on a development branch but never tagged or
released, so its changes ship here too (see the `1.1.0` entry below).

Hardening release candidate: input validation, connection/lifecycle reliability, secret handling
and packaging. Behaviour changes that can affect callers are listed under **Behaviour changes**.

### Added
- `GoveeBleInvalidValue` (a `ValueError` subclass, also a `GoveeBleError`): raised for bad setter
  input: colour channels, kelvin outside the device's `min_kelvin`/`max_kelvin`, segment/zone
  indices and masks, scene payloads, secret length. Builders and AES/RC4 key lengths are validated too.
- `Device.connect()`, an `idle_disconnect` argument on `Device`/`create_device`, and
  `Device.scene_upload_frames(scene)` (the planned upload frames, or `None`).
- `__version__` and more public exports: `Encryption`, `Scene`, `StateCallback`, `profile_for`,
  `GoveeBleInvalidValue`.
- `app_version` argument on the cloud account (overrides the `appVersion` request header).
- `govee-ble-analyze --from-frames-log` input is validated (malformed lines are skipped, reported
  and counted).
- Dev tooling: `dev` and `audit` extras, `scripts/audit.sh`, a `[tool.vulture]` config, an
  independent AES reference test (`pycryptodome`, test-only), ruff/mypy-strict/bandit/pip-audit
  gates in `scripts/check.sh` and CI, and a tag-triggered PyPI trusted-publishing workflow.

### Changed
- `kaitaistruct>=0.11` (was unpinned below); SPDX `license = "MIT"` metadata; classifiers, `Issues`
  URL and a markdown `readme` content type for PyPI.
- The sdist no longer bundles `tests/`; it carries only `src/`, `pyproject.toml`, `README.md`,
  `LICENSE` and `CHANGELOG.md`.
- Profile fields are `Literal`-typed and validated at construction.
- Segment merge / scene planning moved into `wire/` and `scenes/`; `Device` uses public
  `scene_upload_frames`; the private scene alias was removed.
- CI: SHA-pinned actions with minimal permissions, Python 3.11-3.13 matrix, lowest-dependency job;
  optional test dependencies fail instead of skipping when `CI` is set.

### Fixed
- A disconnect racing an in-flight acknowledgement no longer leaves the write waiting or raises a
  non-library error; a link lost while the write lock is held raises a typed error.
- A slow poll can no longer overwrite state set by a newer setter (mutation-sequence guard).
- Relay power: power frames are acknowledged and the resulting state is applied; time sync is
  best-effort.
- Bar-switch devices (H6047): the read-modify-write of the two-bar frame is serialized; the other
  bar's state is read before use.
- Lifecycle leaks: client/link no longer leaked on handshake, device-info and disconnect error
  paths; detached disconnects are bounded in time.
- Plaintext receive queue (no decrypt with a stale session key); a queued frame arriving at the
  deadline is not lost; the end-of-burst sentinel is never dropped.
- Device-info link errors no longer fail `update()`; device info is retried.
- Encryption fallback when the advertised mode is wrong; secret length is checked.
- State callbacks no longer break the notify pump or each other (exceptions are logged).
- Colour groups with an invalid index are skipped instead of producing negative segment indices;
  V1 segment brightness stays `None`.
- Cloud client: response shapes are validated (status checked before JSON parsing, token type
  check); the owned session is closed when login fails; one malformed device secret no longer
  fails the whole account.
- Scene catalogs: SKUs are validated, malformed catalogs and bad base64 raise library errors, and
  catalog writes by `tools/fetch_scenes.py` are atomic.
- Hardware tools: SKU/path validation, argparse, btsnoop length caps, restore-in-`finally`;
  `tools/decrypt_btsnoop.py`/`btsnoop_mtu_scan.py` mask the account secret.

### Security
- Frame-log files are created `0600`, written with `O_NOFOLLOW`, refused if not a regular file,
  bounded (oldest records dropped when full) and flushed at interpreter exit; a dead writer is
  revived. Docs now state that captures are sensitive (the session key is derivable from them).
- Cloud responses are shape-validated; malformed device secrets are never echoed.
- Secret masking in captures shares one implementation and recomputes the frame checksum.
- CI runs `bandit` (Medium+) and `pip-audit`; the three accepted `cryptography` advisories are
  documented (AES-ECB/ARC4 only; PKCS#7/X.509 unused).

### Behaviour changes (may affect callers)
- Bar-switch devices: `set_zone_power` raises when the other bar's state is unknown and cannot be
  read, instead of assuming it is on.
- `GoveeBleAuthError` is no longer retried.
- `send(expect_ack=True)` rejects non-write frames.
- Setters raise `GoveeBleInvalidValue` (still a `ValueError`) for invalid input, including kelvin
  outside the profile's `min_kelvin`/`max_kelvin`. (Brightness percent is still clamped to 1..100.)
- `Capability` and `Encryption` are `StrEnum`: `str(member)` now returns the value
  (e.g. `"power"`), not `"Capability.POWER"`.
- `load_scenes` returns copies (mutating the result does not affect the cache) and raises library
  errors for malformed catalogs.
- Cloud `get_devices` returns `secret=None` and logs a warning for a malformed secret rather than
  failing.

## [1.1.0] — 2026-10-12

Reliability overhaul driven by five days of live frame captures (11 devices, 2 ESPHome proxies +
a local adapter) and validated live on the same hardware: steady-state polls went from ~7.7 s
with a 5 s stall (H60A6) and ~40 % failures to 1.5–4.5 s with no failures.

### Fixed
- **Secret leaked through `wire=` in frame captures.** Only the `plain=` field of the `33 b2` / `aa b1` secret frames was masked; on a plaintext link `wire=` is the same bytes, and on an encrypted link it is decryptable from the captured handshake plus the public PSK. Both fields (and the bad-checksum debug line) are now masked.
- `set_segment_brightness` raised a bare `ValueError` on colour schemes without per-segment
  brightness (H61A8); it now raises `GoveeBleNotSupported`, like `set_segment_color_temp`.
- **Handshake raced the device.** The device delivers its `e7 01` reply twice and re-sends the
  `e7 02` echo ~1 s later. The duplicate step-1 reply was consumed as the step-2 "ack", so the
  first query fired while the confirm was still in flight; ~90 % of H60A6 sessions then lost the
  `0xFF` terminator of their first status burst (5 s stall + retry on every poll). Replies are
  now matched by content, the real confirm is awaited, and late retransmits are dropped.
- Frames failing the XOR checksum are dropped at the notify pump (they were the source of the
  intermittent "unknown proType 0x67/0x7f/0xa5" warnings: handshake echoes decrypted with the
  session key that happened to pass the checksum).
- A link that stops acknowledging writes (half-dead while bleak still reports "connected") is
  torn down and the write retried once on a fresh link, instead of failing the command.
- A disconnect wakes pending waits immediately; a late disconnect callback from a superseded
  client no longer clears the current one.
- The idle-disconnect timer can no longer fire after newer activity (it used to disconnect
  right after a command and force a reconnect); its task is now held so it can't be GC'd.
- `establish_connection` gets `ble_device_callback`, so retries use the freshest proxy path.
- Every public operation has a hard deadline (`OPERATION_TIMEOUT`) and raises only
  `GoveeBleError` subclasses (bleak/asyncio errors no longer leak out of `send`/`query`).
- 0xAC reassembly joins only the contiguous chunk prefix (a gap used to splice later chunks at
  the wrong offset and decode garbage as real state) and decodes a truncated burst up to its last
  whole TLV, so a lost tail yields partial-but-true state without a retry; queries end on a short
  idle gap instead of the full timeout when a terminator is lost.
- 0x0d bulbs (H6006/H6008/H6052): the mode report is `[r, g, b, kelvin u2be]`; white + kelvin is
  colour-temperature mode. H6052's "mechanism C" decoded it as RGB-only (reporting white instead
  of 2700 K, live capture `aa 05 0d ff ff ff 0a 8c`) and issued a duplicate mode query; all three
  bulbs now read colour/CCT back from the report they already send.
- Polled devices always re-read power (previously skipped once a device was off, so a light
  turned on externally was never noticed).
- H6047: changing one bar no longer assumes the other bar is ON when its state is unknown (it
  turned the other bar on); the bar state is read back (`aa 36`) and polled.
- Advertised on/off is ignored for 15 s after a command/read (stale broadcasts flipped state).
- Brightness is never sent as 0; the reported-brightness scale is an explicit profile field
  (`brightness_max`) instead of a `>100 means 0-255` guess.
- Scene uploads are paced instead of blasted (dropped frames over proxies).
- Account-lock secrets are masked in frame captures (logs/diagnostics).
- `FrameLog` file writes moved off the event loop.
- Cloud client: request timeouts, the documented-but-missing re-login on 401, no response bodies
  in error messages, no forced login when a token is supplied.

### Added
- `Device.turn_on(brightness=, rgb=, kelvin=, power=)` / `turn_off()` and
  `GoveeConnection.send_many()`: multi-attribute changes go out as one batched, ack-verified
  operation on one connection. Whole-fixture power is always one frame: H60A6 uses the global
  `33 01` switch (live-verified to drive both zones: off clears them, on lights both even when
  each was off; previously one `33 30` frame per zone), H6047 the combined `33 36` bar frame.
- `GoveeBleAuthError` is raised when a device rejects its secret key.
- `Device.encryption` (the negotiated channel mode); colour temperature read back from the mode
  report; segment writes update `state.segments`.
- `tools/power_semantics_probe.py`: hardware experiment for global vs. zone power.
- Fake-peripheral test harness (`tests/fake_ble.py`) reproducing the captured failure modes;
  release workflow publishing to PyPI via trusted publishing.

### Removed
- Dead code: `ble/frame.py`, duplicate `checksum_ok`, unused `CONNECT_TIMEOUT`.

## [1.0.3] — 2026-07-12

Field-tested and confirmed live on H60A6 before merge (see PR discussion): a truncated 9-of-11-chunk
burst recovered fully on retry with no warning; an H6047/H6641-style zero-chunk case correctly
logged `state left stale` with chunk indices after the retry, rather than papering over it.

### Fixed
- `Device._read_status()` (the `0xAC` status read-back for `readback="status"` devices, e.g. H60A6)
  now **retries once** on an empty parse before leaving state stale. A dropped BLE notification
  mid-burst yields a truncated or empty reassembled buffer — reported in the field as
  `status reply did not parse (0 bytes): ... requested 1 bytes, but only 0 bytes available` and
  `status reply did not parse (148 bytes): ... requested 17 bytes, but only 12 bytes available`
  (`wire/reassemble.py` DEBUG log). This was already non-fatal (fields are applied conditionally,
  so a failed parse can't erase known-good state) and already surfaced a WARNING
  (`"... state left stale"`), but a single dropped notification cost a whole poll cycle; a fresh
  query is independent and usually succeeds immediately. Both the retry-attempt DEBUG line and the
  final WARNING now also report which `0xAC` chunk indices were actually received (e.g.
  `0x00,0x01,0x02,0x04,0xff`, or `<none>`), to distinguish a dropped-mid-burst from a
  completely-empty read on any future occurrence.

## [1.0.2] — 2026-07-12

### Fixed
- **H6047/H6641's `1.0.1` colour read-back fix reused the wrong controller's decoder.** `1.0.1`
  correctly identified that neither SKU dispatches the `0xAC` status burst, but wired their
  per-segment colour read-back onto H61A8's `mechanism_b` (`BulbGroupColorV2`) decoder —
  a *different, unrelated controller* that only happens to share the `0xA5` wire opcode with
  the one H6047/H6641 actually use (`Controller4ColorInfoByGroup`, confirmed via a full re-read
  of `devices.yaml`: *"SAME PARSER ... but TWO different TRANSPORTS"*, explicitly distinct from
  H61A8's separately-listed `BulbGroupColor`/mechanism-B). The two decoders disagree on group
  size (3 vs 4), so segment indices were silently misassigned. Caught by the test suite itself
  before any further guessing — a second wrong per-batch value (5, conflating two unrelated
  "5"s from two different code paths) was caught the same way and never shipped.
- New `color_readback="mechanism_a_direct"` (`wire.parse.parse_direct_color_group`,
  `Device._read_mechanism_a_direct`) correctly decodes `Controller4ColorInfoByGroup`'s per-group
  reply — group size **4**, source-confirmed independently for both SKUs
  (`devices.yaml`: *"4/group"* / *"4 records/group"*), not extrapolated from H61A8.
- **H6641's group-count approximation is now resolved, not just documented**: a live `0x40`
  IC-count read (`build.ic_count_query`, `wire.parse.parse_ic_count`, the already-modeled
  `ic_segment_read` ksy type) returns the device's own precomputed group count directly —
  no client-side ceil-division/divisor guess needed. New `DeviceProfile.color_readback_live_ic`
  flag gates this per-SKU (H6641 only; H6047 keeps its static, source-confirmed piece count).
- `Device._read_mechanism_b` is now H61A8-only again (its docstring no longer over-claims
  sharing with H6047/H6641).

## [1.0.1] — 2026-07-12

### Fixed
- **H6047 and H6641 never actually answer the `0xAC` status burst** — they were wrongly modeled
  as mechanism-A (the same `0xAC → 0xA5` path as H60A6), which produced a deterministic
  zero-frame read on real hardware (source-confirmed, not a wire/timing issue: `H6047`, goodsType
  119, routes to `Compose4InfoBleIot`, `Support.isGoodsTypeH6047:177`; `H6641`, goodsType 247,
  never reaches the `afterConnected` `0xAC` dispatch at all, `H61D3Support.f0(247)=false`). Both
  read per-segment colour via **direct per-group requests** instead (`AA A5 <group>`,
  `Controller4ColorInfoByGroup` — the same decode H61A8 already used as `mechanism_b`).
  `DeviceProfile.readback` for both switches `"status"` → `"polled"` (power/brightness/scene via
  `aa 01/04/05`); `color_readback="mechanism_b"` added for both, reusing the existing decoder.
  New `DeviceProfile.color_readback_segments` field lets H6047's batch math use its true
  read-back piece count (`getColorPieceSize`=12) instead of its write/addressable count (10),
  which differ for this SKU.
  **Two related approximations remain open (flagged in `profile.py`, not fixed by this change):**
  H6641's true colour-group *count* is IC-driven (needs a live `0x40` read this library doesn't
  perform yet — falls back to the write-mask width as an approximation), and its per-reply
  *record count* assumption (3, matching H61A8's `BulbStringColorControllerV2`) is unconfirmed —
  H6641 may use an unmodeled `V3` controller with a different count/layout. Neither SKU is yet
  live-verified.

### Spec
- `devices.yaml` / `docs/GOVEE_BLE_GATT_PROTOCOL.md`: documented the H6047/H6641 `0xAC`
  non-dispatch (source citations above) and their `per_segment_color_read` direct-request
  transport; reconciled `H61D3Support.e()`'s divisor (`d ∈ {3,4}` → `d ∈ {3,4,5}`, H6641/247 = 5)
  against the `ceil(IC/5)` group-count math.

## [1.0.0] — 2026-07-11

First public release. Prior `v1`/`v2`/`v3` were internal rewrite generations (never tagged or
published); `1.0.0` is the initial Semantic-Versioning baseline, committing to the
`govee_ble_local.__all__` public API. This release is the ground-up **v3-generation rewrite**: a
data-driven `DeviceProfile` table + one capability-gated `Device` class over a spec-first `wire/`
layer (build / parse / reassemble) and the shipped, ksy-generated readers. Clean break from the
old `GoveeBleClient` API.

### Added
- Capability-driven `Device` + `create_device()` / `discover()` / `DeviceProfile` public API.
- Colour temperature per zone/segment: `Device.set_zone_color_temp()` / `set_segment_color_temp()`
  + `build.segment_color_temp()` (the 0x15 CCT frame's segment mask, previously all-segments only).
- Read-back: plug relay power, H6047 segment colours (mechanism-A status), and device-info
  (`serial` / `wifi_mac` / `firmware` / `hardware`), plus `DeviceState.ble_mac`.
- `Device.set_gradual()` — the `0xA3` gradual/fade-on-BLE↔wifi-handoff flag (curated: H61A8;
  read back into `DeviceState.gradual` on `update()`).
- `Device.read_secret()` and `Device.ingest_advertisement()` restored (v2 parity).
- Diagnostics: a coherent `govee_ble_local.*` log-level scheme, a `govee_ble_local.frames`
  frame-tier logger for full-session capture (incl. over a Home Assistant Bluetooth proxy),
  the `govee-ble-analyze` console script + a frames-log→JSONL converter, and `docs/DIAGNOSTICS.md`.
- Live H60A6 tools: CCT choreography + a segment-map probe.

### Changed
- The reassembled `0xAC` status buffer is now parsed **entirely by the generated `StatusReply`
  reader** — the hand TLV-walk (`walk_tlvs`), `_add_color_group`, and the MAC-anchor device-info
  heuristic are all retired. Only the cross-frame de-chunk stays hand-done (Kaitai can't join
  frames). The offline analyzer uses the same reader.
- README + version brought to the v3 API; description now "Govee devices" (plugs included).
- **Dependency hygiene:** `PyYAML` moved to the `test` extra — it isn't used at runtime (the device
  table is hardcoded Python; `yaml` appears only in the test suite). Runtime deps are now
  `bleak` / `bleak-retry-connector` / `cryptography` (HA defaults) + `kaitaistruct` (the one accepted
  pure-Python exception). Added `[project.urls]`.
- **Docs:** README install-from-source instructions (not yet on PyPI); a `pdoc` API reference
  (`.[docs]` extra + `tools/gen_docs.sh`); a `CONTRIBUTING.md` (branching / commit / changelog /
  release conventions); project conventions recorded for maintainers.

### Fixed
- H60A6 segment→zone map corrected (index 12 = main panel, 0–11 = background ring), making
  per-zone colour/CCT genuinely independent — live-verified.
- H60A6 `wifi_mac` / `hardware_version` read-back (regressed in the v3 clean break) restored,
  now from the modeled `0x07` device-info TLV; zeroed `aa 07` replies no longer clobber real
  values (`0.00.00` / all-zero → None).
- `reassemble()` de-duplicates doubled status chunks (devices double-deliver notifications).

### Spec
- Modeled the reassembled `0xAC` nested TLV values (source-verified, `Compose4BaseInfoSingleRead`):
  `status_tlv` switches `0x05→mode_status`, `0x07→device_info_read`, `0xA5→color_group_status`;
  `status_reply` uses `repeat: until type==0` + `if: type != 0` to survive the trailing zero-pad.
- Added `gradual_read` (0xA3) — now surfaced (`Device.set_gradual`) — and tag-grouped
  `color_strip_write`, which is modeled but **not exposed**: it has no curated-SKU binding in
  `devices.yaml` (needs source: which SKU uses `MultipleColorStripControllerV1` / comType 0x40, and
  whether it complements or replaces `set_segment_rgb`).
- Fixed a YAML-1.1 boolean id trap (`id: on` → parsed as `.true`); renamed to `state` / `on_flag`.
- `devices.yaml`: H60A6 segment→zone map; H6641 colour records are 4-byte; mechanism-B/C read-back
  and scene-dialect notes.
- Adopted the parametric **Tier-C** grammar variants (`op15_color_typed`, `mode_color_0d_typed`;
  joining `color_group_read`) so the ksy owns 100% of byte structure — the client passes one
  discriminator and the branch runs inside the grammar. `devices.yaml` gained per-family
  `client_profile` blocks (discriminators as data) + schema support. Readers regenerated; additive,
  no runtime behaviour change.
- `spec/` is now a self-documenting protocol package: added the canonical protocol docs
  (`spec/docs/GOVEE_BLE_GATT_PROTOCOL.md`, `SCENE_UPLOAD_ENCODING.md`, `USING_THE_KSY.md`) and an
  executable reference codec (`spec/govee_reference.py`) — the framing layer Kaitai can't express
  (AES-ECB+RC4 decrypt, `0xAC` reassemble, XOR BCC) + dispatch wiring, with a self-test.
