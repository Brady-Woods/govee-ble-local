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

### Changed
- **A Device now re-learns its command channel from later advertisements, for its next session, as the app
  does.** Until now the cipher was fixed when the `Device` was built: its advertisement's encrypt flag, else the
  profile default. A device built before any of its advertisements was heard stayed on that default for the
  Device's life. Such a device is an H6008, whose default is plaintext but whose SW1.01.25 firmware encrypts and
  answers nothing else. The same goes for any SKU whose default is plaintext while its firmware encrypts.
  `Device.ingest_advertisement` now reads the encrypt flag too, whatever the advertised name, and hands it to the
  connection (new `GoveeConnection.set_advertised_encryption`, which returns the mode the next session uses).
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
