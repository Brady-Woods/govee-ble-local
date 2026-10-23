"""PROCEDURAL: devices.yaml's supported rows resolved into DeviceProfile facts (standard library only).

The input is ``_generated/devices.py``, which ``tools/gen_devices.py`` projects from ``spec/devices.yaml``:
the families with a ``client_profile.by_goods_type`` row that carries ``support``, typed by the TypedDicts
below. :func:`profiles` gives one field mapping per group of (row, SKU) routes whose resolved facts are
identical, with each route's goodsType and accepted pacts, which ``devices/profile.py`` turns into the
:data:`~.profile.PROFILES` table and its routing index. The generator runs :func:`routes` over the whole
document and the same :func:`profiles` over its projection, every support level, before it writes the
module, so a missing fact, or one the library can't serve, fails the build with :class:`SpecError` and never
an import. Nothing here imports the package, so the generator can load this file on its own. The generator
also runs :func:`catalogue` over the whole document with the committed catalogue snapshot
(``spec/govee_catalog.json``), and the rows' ``models`` in the projection carry the SKUs it routes.

The spec's resolution (devices.schema.json ``clientRow``, ``spec/govee_reference.py`` ``ble_rows`` /
``client_row``): a row is a BLE route for its models when it has ``support``, ``pacts`` and ``ble`` in its
(else the family's) connectivity. A SKU that several rows list has one route per row; the client picks
among them by the advertised pact, else the goodsType (``profile.profile_for``), and :func:`routes` gives
the SKUs that one row routes. A row's facts are its own, else the family ``client_profile``'s, and the
layered ones (scene_versions, kelvin_range, brightness_range) fall back to the family ``specs``, with a
SKU's ``by_model`` Kelvin and brightness ranges between the two (the schema's by_model, which the row
wins over; ``client_row``'s ``sku``). The wire facts layer the family
``wire``, ``by_model.<SKU>`` and ``by_goods_type.<gt>``. A profile is resolved with
the pact and the firmware unknown; the pact- and firmware-gated facts the library follows at run time are
named below.

Library rules over the spec's vocabulary (what the library can't serve yet, or how it serves it). A row is
admitted only when every value it holds is one the library implements, and a fact the library needs and
the row lacks fails the build: nothing defaults silently.

- Admission: ``hardware_verified`` and ``app_traced`` rows (:data:`ADMITTED`); a ``modelled`` row
  (:data:`MODELLED`) only where the caller opts in (the user's "app-traced by default"). Every row with
  support is resolved and checked, whatever its level. A row with support that is no BLE route (no models,
  pacts or ble link) raises, and so does an ``assigned_goods_type`` item in any family that names a routed
  SKU (case-insensitively), unless its condition tests the pact alone or it is a pairing write with a pact:
  the library routes by SKU and pact, so it can't follow a firmware-gated rewrite or a pairing write with
  no pact. A pairing (``bind``) item's ``pact`` is an exact-pact condition (:func:`bind_pact`): the app
  writes its goodsType into the record only at exactly that advertised pact (``NewRgb2TLUtil.isNewRgb2TL`` /
  ``isNewRgb2TL4BleIot``, an exact compare), so it says nothing about the SKU at any other pact. A row of
  another goodsType that lists one of its SKUs and accepts that pact (or ``any``) raises, and a (SKU, pact)
  route no row of its goodsType carries is for review (:func:`bind_review`; H615A-E and H613A-F at
  (65535, 255) are). Each route of its SKUs needs an advertised pact (``Route.needs_pact``,
  :func:`bind_skus`): without one the library can't tell the row's pact from the write's, where the app runs
  other facts (H615A-E at (65535, 255): colour version 4 and raw brightness, ``Config.Zv`` / ``Config.aw``,
  ``Support4StringLightV2.b``), as it can't pick between two rows that differ. A pact-conditioned item is
  routing data instead: its SKUs are listed on the row of its
  goodsType with the pacts at or above its gate, and on a row of a goodsType it rewrites from with pacts
  below it, so the
  advertised pact picks the row the app's rewrite picks. A row that lists one of them with pacts on the
  wrong side raises, and a route no row carries yet is for review (:func:`reassignment_review`). Those SKUs
  are outside the pact fallback (below), so a pact below the gate never reaches the target row, and nor does
  an advertisement without one: the app rewrites only a record whose pact meets the gate, and a record without
  one holds 0 (``SkuToGoodsTypeUtil.f``:66-99 over ``OldRgbBkUtil.a`` / ``.b``:75-97, the pact
  ``OldBleModel.itemClickOp``:52-60 copies, the int fields ``Info4BleIotDevice.p`` / ``.v``:90-91), so it
  runs the source goodsType. A target-row route needs an advertised pact too (``Route.needs_pact``), and
  ``profile.profile_for`` takes it only with one. So does each route of a ``pact_required`` row: the app also
  accepts the row's goodsType at pacts that run a path the row doesn't serve (straightfloorlamp 69's (1,1)),
  so a unit advertising no pact may be one of them. ``pact_required`` with ``pacts: any`` raises. A SKU's
  level is its row's, or its ``by_model`` ``support`` (:func:`sku_support`), on a SKU one row of the family
  lists, where its own fact is inferred and the row's other SKUs' are traced (h6609 260: five SKUs' IC-gated
  read-back). A pact branch's ``support`` (:func:`pact_support`) is the row's level at that pact alone, where
  the screen's per-unit gates resolve to the branch the row states only at it (dreamcolorlightv2 21 at (2,1)):
  the SKU routes at each pact at its level, and without a pact at the row's own level, so a route a branch
  alone admits needs the pact (``Route.needs_pact``). Such a row lists its pacts (not ``any``), and its SKUs
  have no ``by_model`` ``support``. A SKU that a ``modelled`` row and an admitted row both list raises too:
  the spec's routing (``ble_rows``) counts both wherever both accept the pact, and the opt-in must leave the
  admitted routes as they are; one row's pact branches split its pacts, which no two routes share.
- The pact fallback (decision D3), the 1.x compatibility rule: 1.4.0 routed each of its 18 SKUs whatever
  pact its advertisement carried, so ``profile.profile_for`` still routes one of them
  (:data:`PACT_FALLBACK_SKUS`, frozen at those 18) to its only admitted profile at a pact no row accepts, and
  no released unit is demoted by a pact its row doesn't list. Every other SKU (one a row lists, one only the
  catalogue snapshot routes, one of a reassignment) is served only at its rows' pacts, as the app's scan
  serves it: a device whose advertised pact its goodsType doesn't accept is refused
  (``AbsBaseBleModel.e``:158-174, ``checkProtocol`` -> ``Pact.i``), and the accepted pacts compare exactly
  (``Protocol.c``, Protocol.java:41-48).
- The catalogue snapshot (:func:`catalogue`, the precedence of ``spec/govee_catalog.json``'s doc): a SKU no
  row lists routes to the BLE row with support at its catalogue goodsType when that is the only row at it
  (the app's ``Pact.f``), with its facts, and never at goodsType 0 (the legacy code). Its support level is
  the row's capped at ``app_traced`` (:func:`catalogue_support`): the snapshot's goodsType is no bench check,
  so a ``hardware_verified`` row's evidence is not the SKU's, and a ``modelled`` row's SKU stays behind the
  opt-in. Such a SKU is served only at the row's pacts, outside the pact fallback. A SKU a row lists
  keeps the spec's route, unless its catalogue goodsType is a conflict (non-zero, and none of its rows'),
  which only an ``assigned_goods_type`` item citing the app's rewrite of it settles: one whose
  ``from_goods_type`` names the catalogue goodsType, or, with none, one that the pact conditions (a
  pairing write, which applies at its own pact alone, settles nothing). An unsettled one is unrouted and on
  the
  generator's review list. The snapshot's ``ic`` is read nowhere: no row's app trace cites the app's read
  of it (``CategoriesConfig.getDefIcNum``), and no count is derived from it.
- A row's colour scheme drops the capabilities it has no frame for (:data:`SCHEME_LACKS`), but only a
  scheme the row names itself: a family default that would drop one raises. A traced ``color_functions``
  agrees with the capabilities: ``color_temperature`` with ``color_temp``, and ``segment_brightness`` needs
  ``segments`` where the row's scheme has a segment-brightness frame (:data:`SEGMENT_BRIGHTNESS_SCHEMES`).
  Elsewhere the flag alone calls for nothing: the slider has no frame the library could send (H6071 /
  H6075's one-piece kelvin_v9 lamps, with no relative-brightness version; h61a8's 0x0b segment colour). A
  row with ``segments`` offers segment brightness (``DeviceProfile.segment_brightness``, the ``15 02``
  slider) only where its scheme has that frame and its colour page the slider: a traced
  ``color_functions`` without ``segment_brightness`` turns it off (h61d3 202: ``Config.I[202][3]`` false
  hides it), and an untraced one keeps the 1.x rule (on); segment colour stays. ``rgb``, ``color_temp`` or
  ``segments`` on a relay row raises (the library resolves no colour scheme or Kelvin range for a plug), and
  elsewhere they need a colour scheme. A row's ``segment_brightness_groups`` (the strip's
  ``canClickBrightness`` groups) becomes ``DeviceProfile.segment_brightness_groups``, which the
  segment-brightness write widens a selection to, on a row with that frame and ``segments`` only, as disjoint
  groups of the row's segments. A row that keeps ``segments`` and ``color_temp`` after those drops says
  whether its screen sends a Kelvin over a piece selection (``segment_color_temp``, which becomes
  ``DeviceProfile.segment_color_temp``: the Part tab's Kelvin item, ``DelegateColorV2Part.Z0`` ->
  ``LightColorCapabilityCenter.C2``); a row that keeps fewer may not say, and a row with a
  ``color_temp_control`` (below) says false. ``color_temp`` needs a Kelvin range, and so does ``rgb``
  under every scheme with a colour-temperature mode (all but h61a8, :data:`SCHEME_LACKS`): its RGB writer
  runs the app's Kelvin-tint test on every pick whatever the screen offers (``Config.Yt`` / ``bu`` / ``Zt`` /
  ``fu``:
  ``KelvinConfig.i``; the H7310 / H7318 / H6165 rows have no Kelvin slider). A row's ``white_type`` (the slot
  of the h60a6 colour-temperature frame, Bytes.r's white type: ``DeviceProfile.white_type``) other than
  ``rgb_white``, the default, needs the h60a6 scheme. A row's ``color_temp_control: illumination`` (the screen
  sets colour temperature on its illumination tab alone, ``View4IlluminationMode.I0``: the Kelvin frame, then
  ``33 04`` <the current brightness>) becomes ``DeviceProfile.kelvin_with_brightness``. It needs the
  ``color_temp`` and ``brightness`` capabilities, percent brightness with no pact branch (so the held value
  round-trips), and the h60a6, h6188 or kelvin_v9 scheme, whose colour-temperature frame is the tab's builder
  (h60a6 not at ``rgb_self_tem_color``, whose tab slot is ``ff ff ff``); the colour page's
  ``color_temperature`` flag then doesn't decide ``color_temp`` (``color_functions``).
  ``color_temp_control: whole_device`` (the
  colour page's Kelvin pick always writes every piece, as h61d3 ``ColorMode.canClickColorTem`` does on 239 /
  246) needs ``color_temp``. Either value becomes ``DeviceProfile.whole_device_kelvin``: no Kelvin on part of
  the device. A row's ``kelvin_readback: pieces`` (the colour-temperature state read from the read-back's
  pieces, goodsType 180's ``VM4Light.g5``) becomes ``DeviceProfile.kelvin_from_pieces``; it needs
  ``color_temp`` and the direct colour read-back on a polled row, over the static or device-reported pieces
  (not an IC-sized walk). An ``untraced`` scene dialect drops ``scenes``; ``none`` is
  activate only. Scenes need a bundled catalog, as every SKU of 1.4.0 that offers them ships one: a SKU the
  scene store has no catalog for (``scenes/store.json``, the generated ``SCENE_CATALOGS``, which
  :func:`profiles` takes as ``catalogued``) is offered no ``scenes``, its row's scene facts still checked.
  An ``untraced`` colour parse on a row with ``rgb`` or ``color_temp`` raises (``none`` is a traced value:
  the screen parses no colour).
- The reads the library makes: the 0xAC status read is the row's ``status_read``, in its order
  (``DeviceProfile.status_read``, ``build.status_query(types=)``; ``[]`` is the bare ``ac 03 00``, the h70dx
  screen's, which requests no type), and its reply is read with the row's own wire facts
  (``DeviceProfile.status_decode``, ``reassemble.status_profile``): the forms of the TLVs the status stream
  reads (:data:`_STATUS_TLV_FORMS`), the colour-group form where it reads the 0xA5 TLV as colour groups (under
  ``f_a5: color_group``, a requested ``color_group`` or any 0xA5 the bare read's reply carries, as the
  h70dx screen parses each, ``VM4H70DX.g5``), or the one-record form under ``f_a5: single_record`` (one
  ``[brightness, r, g, b]`` record at bytes 1-4, the H703A screen's
  ``$afterConnectedSingleReadDeviceInfo$1.n`` over ``SubMode4Color.l``: a requested ``color_group`` over one
  piece, and never a direct read; any other 0xA5 is skipped), and the TLV 0x05's mode decode
  (mode_status over mode). The library takes the zone bits from TLV 0x30, so a
  requested ``cmd_30`` needs ``f_30: zone_power`` (at any pact). A row's pact branches that only drop
  ``color_group`` become ``status_color_groups_pact`` (one pact type, from its lowest code that keeps it:
  H60A6's); otherwise each branch with another read becomes that pact's exact read
  (``DeviceProfile.status_read_by_pact``, :func:`_status_branches`), which a SKU of the pact fallback (below)
  can't take, and the forms and the colour-group decode serve every read. A requested ``cmd_a1`` and the wire
  ``f_a1: segment_info`` go together: that TLV's count is the device's segment count
  (``DeviceProfile.status_segment_count``: the H707A screen's colour page, ``H707AInfo4Detail.G0``; a reply
  without it takes TLV 0x40's segment byte, read under ``f_40: ic_pair``, where its IC changed), and the
  wire ``num_segments`` is the count before the first read. A polled row
  reads its power from aa 01, or, with ``power_read: aa34_detail``, from the aa 34 device-detail read's
  light flag (``DeviceProfile.detail_power_read``; wire ``f_34: device_detail``; never on a bar row, and with
  no aa 01 to take a ``heartbeat_aa01`` power from), which goes with ``power_write: light_switch``: the power
  write is then the H6057 light switch, ``33 30 <1 / 0>`` (``DeviceProfile.light_switch_power``; wire
  ``f_30: light_switch``; never on a zoned row), whose state that flag is. A polled row
  with the brightness capability reads aa 04 on every poll (one without it reads none, as H7004's screen
  doesn't: ``ControllerStrategyConfig.k1``) and a gradual row ``aa a3``, so ``connect_reads`` lists
  ``brightness`` exactly on a polled row with the capability and ``gradual`` wherever the capability is; a
  ``gradual`` read without the capability (the screen's default ``Compose4InfoBleIot`` read, with no
  gradual control) is made on every poll too and its
  reply discarded (``DeviceProfile.gradual_read_only``), on a polled light row only: that is where the
  default read is traced (the h7050 screens), and a status or plug row's reads are its own strategy's. An
  ``ic_num`` read goes with ``ic_pieces`` and the direct colour read-back: an object form's rule over the IC
  count sizes the read-back's pieces (``DeviceProfile.color_readback_live_ic``: the count clamped and divided,
  rounding up, or down under ``rounding: floor``, H6641 and the h705a rows, an IC of 0 no count under
  ``skip_zero``, h705a 184; an IC table, H6609; or two strips' counts capped and summed, H1A45:
  ``_readback.live_ic_pieces``; on a row whose wire ``f_40`` is ``ic_pair`` the screen reads the reply with
  the IC-pair reader, ``DeviceProfile.color_readback_ic_pair_reader``, no count where it throws, which the
  pair rule needs; a SKU's ``by_model`` ``ic_pieces`` and ``connect_reads`` replace its row's, on a SKU one
  row lists, where the screen sizes the pieces or adds the aa 40 read by SKU: h6609 ``H70c4Support.b`` at
  260, rgbiclight ``ControllerStrategyConfig.E1``'s ``supportBindWithIcNum`` / ``supportIcFresh`` at 71 /
  123), and, but under a table's ``readback_only`` (``DeviceProfile.color_readback_ic_walk_only``: the walk
  alone; the writes keep the static pieces, a read with no count walks them, ``_readback.walk_pieces``, and
  a walk of other than the strip's pieces is not applied), the writes (``color_writes_live_ic``:
  each poll's pieces are ``Device.segment_count``, the whole-device mask and the segment index bound, as the
  screen's strip takes them from the stored IC, ``ColorPieceConfig.c``; the wire ``num_segments`` is only the
  count before the first read), or, with ``ic_pieces: sections``, its two section bytes size the read-back's
  alone (``DeviceProfile.color_readback_ic_sections``), or, with ``ic_pieces: fixed``, it is made on every
  poll and discarded (``DeviceProfile.ic_num_read_only``: the pieces are the static count, or the
  ``special_info`` read's). A status row takes an object form with one IC source: the status read's TLV 0x40
  (a requested ``ic_num``, read as its wire ``f_40`` ``one_count`` / ``one_count_u16`` says; a TLV 0x40 ahead
  of the 0xA5 TLVs sizes the same burst's colour groups, as the screen's lambda stores the count before it
  reads them), or aa 40 reads around the status read (``connect_reads`` / ``post_status_reads`` ``ic_num``,
  ``DeviceProfile.ic_reads_around_status``: the h7020 4702ABC connect). Its pieces size the writes and the
  status read's colour groups, or with ``readback_pieces: ic_count`` the groups are placed over the IC count
  itself (``DeviceProfile.color_readback_ic_raw``: ``Support.getColorPieceSize(gt, IC)``). A ``special_info``
  read (the 0xAC special-info read ``ac a1``, whose segment IC record gives the device's segment count, the
  goodsType 180 screen's: ``DeviceProfile.special_info_read``) sizes the masks and the walk as the aa 41
  section count does, so it goes on a polled row with ``segments``, never with that read or an IC-sized
  read-back. A ``light_num`` read (aa 0f, the string count) goes with ``light_num_pieces``, on a polled light
  row only (the h7020 bulb-string screens' connect, ``NewDetailVM.s5``) and as the row's one live count (no
  ``ic_pieces`` object form, no aa 41 section count, no ``special_info`` read): the count times ``per_unit``
  is the screen's stored IC (``NewDetailVM.u5``), so it sizes the writes and the direct read-back as an
  object form's pieces do (``DeviceProfile.light_num_per_unit``), and the wire ``num_segments`` is the count
  before the first read. Every command
  form the library sends or reads for the row must be the row's (wire ``forms``), a polled row's aa 05 reply
  is read with the row's wire ``mode`` (``DeviceProfile.mode_decode``, over the spec's default,
  :data:`_MODE_UNSET`), and a colour-group read is of 4-byte records in the row's wire
  ``group.num_per_group`` (and, in the status read, its ``group.rule``).
  Every status row names its ``device_info_reads`` and no other row does: whether the library adds the aa 06 /
  aa 07 03 / aa 07 02 device-info reads it makes on every other row (``DeviceProfile.device_info_reads``). At
  ``none`` it doesn't, and takes the versions and device id from the status reply's TLV 0x07 alone, as the
  screen does (``Compose4BaseInfoSingleRead.w``); at ``firmware`` (a FIRMWARE-SOURCED extension the row
  cites: H60A6, H6076) it does. There is no third value: without that evidence a status row sends none.
- The writes on every connect (``connect_writes``): a plug's ``plug_sync_time``, and on a light nothing or
  ``light_effect_stop`` (``DeviceProfile.light_effect_stop``), each sent once per session after the
  handshake and any unlock. A SKU's ``by_model`` ``connect_writes`` replaces its row's, where the screen
  splits them by SKU within one goodsType (bulblight ``Support.c`` at 342), on a SKU one row of the family
  lists (a ``by_model`` entry is family-wide; ``connect_reads``, ``connect_writes``, ``ic_pieces`` and
  ``support`` on another SKU raise). A SKU's ``by_model`` ``scene_power_on`` (the power-on ahead
  of a scene apply, H6009 / H6010) becomes ``DeviceProfile.scene_power_on``, on a row with ``scenes`` only.
- The keep-alive: every row names the read its screen sends while the link is up (``heartbeat``, no family
  default), which becomes ``DeviceProfile.heartbeat`` (its command code), and its ``heartbeat_timing``
  (``fixed_rate`` unless the row says ``after_quiet``). A row's live power checks it, one way only: a
  ``heartbeat_aa01`` / ``heartbeat_aa36`` / ``heartbeat_aa33`` power_live needs the heartbeat ``power`` /
  ``cmd_36`` / ``volume``, and a relay row ``power`` (a plug's outlets are the aa 01 reply's); a
  ``push_30``-only row may send any. A status row that says ``heartbeat_power`` (an opt-in, served only with
  ``heartbeat_aa01``, heartbeat ``power`` and no zones: the reply is the one switch) follows the screen: the
  status read once per link, then the power from each aa 01 keep-alive reply (``ShareVM.N3`` -> ``J3``;
  ``DeviceProfile.heartbeat_power``): straightfloorlamp 69, h6069 284, rgbiclight 351 / 352, h7086 293, h7020
  296, h7076 313, h3700 383 (the user's decision 17), h7020 316 / 318 / 337 / 391 / 411 (decision 26) and
  h61d3 263. Such a row's ``heartbeat_reread`` (only with ``heartbeat_power``, and with the wire group's
  ``num_per_group``, the re-read's group size: it becomes ``DeviceProfile.color_readback_per_batch``) is the
  screen's re-read on a power change (``J3`` -> ``Compose4Off2On``: aa 05, then the screen's aa a5 groups over
  its ``pieces``, ``DeviceProfile.heartbeat_reread_pieces``); a row without it sets the switch alone. The
  library folds no other keep-alive reply and no ee 30 push into state: the poll's own power read stands in
  for them, aa 01 on a polled row and the status read's TLV 0x01 on a status row (``heartbeat_aa01`` on
  either), the aa 36 bar read for ``heartbeat_aa36``, and either for a ``push_30`` with its wire ``f_ee_30``.
- The session (:data:`SESSIONS`): a row is ``held`` unless it says ``session: per_command``, the device-list
  card's path, where the control screen has no BLE control (DECISIONS.md 6). Such a row reads nothing over
  GATT (``power_read: advert``, the advert's switch byte: ``DeviceProfile.readback`` ``"none"``), writes
  nothing on connect, holds no link (``heartbeat: none``: ``DeviceProfile.heartbeat`` None) and carries only
  power and brightness, the card's ``33 01`` / ``33 04``; the library sends each of its writes on a session of
  its own (``DeviceProfile.session``, ``GoveeConnection.send_once``). Anything else on it (a read, a connect
  write, a lock, zones, pieces, another capability, a relay) is not served yet, and ``advert`` and ``none``
  on a held row are not served either: the spec keeps the two facts independent (a firmware-confirmed read
  on the card's session is a later item).
- Wire branches: the library reads the wire with the pact and the firmware unknown, so a wire ``by_pact`` /
  ``by_firmware`` branch raises, but for the unread ones of :data:`_UNREAD_WIRE_BRANCHES` (H60A6 pact (1,
  1)'s one colour piece: decision D3, the library keeps the row's 13).
- Pact branches are at the row's listed pacts only (a branch elsewhere is reached by no device: it raises),
  and a family with a row with support takes ``by_model`` / ``wire.by_model`` entries only for SKUs one of
  its rows lists or the catalogue snapshot routes to one of them (:func:`sku_entries`). ``status_read``
  becomes the gate or the exact-pact reads above, and ``scene_versions`` becomes
  ``scene_versions_by_pact``, which ``Device.scene_versions`` applies with the advertised pact before the
  firmware entries;
  ``kelvin_range`` and ``brightness_range`` become
  ``kelvin_by_pact`` / ``brightness_by_pact``, which ``Device`` applies with the advertised pact the same
  way (goodsType 261 at pact (4, 1); a pact Kelvin range needs a base one and no Kelvin firmware branch, a
  pact brightness range the brightness capability); ``capabilities`` take the row's own values only on the
  D3 branch, pinned exactly as the wire one is (:data:`_UNREAD_PACT_CAPABILITIES`: H60A6 pact (1, 1) keeps
  the row's capabilities), and raise elsewhere or when that branch changes; a branch on any other fact
  raises.
- Firmware branches resolve after the device-info read (D6): a ``kelvin_range`` item gated on the hardware
  version alone becomes ``kelvin_by_hardware``, and one gated on the soft version alone
  ``kelvin_by_software``, compared as the item's ``compare`` says (``DeviceProfile.kelvin_version_compare``,
  :data:`KELVIN_COMPARES`). ``string``, the default, is ``KelvinConfig.Config.z2`` / ``Z2`` / ``X2``'s
  ``compareTo``, so its gate must be in the app's ``a.bb.cc`` form, two-digit fields, for the string compare
  to order it. ``numeric`` is ``Config.Y2``'s ``BleUtils.isHigherVersion`` (:func:`parse_version` of each,
  at least the gate's: h60ax 235). A row's items are on one compare, and ``compare`` on any other item raises.
  They are the row's, or the SKU's ``by_model`` ones over its own ``kelvin_range`` (bulblightv3 H14A1), never
  both, and on one version. A ``by_model`` range under the row's own (which wins) raises. A
  ``scene_versions`` item gated on the soft and / or hard version becomes ``scene_versions_by_firmware`` (the
  dreamcolorlightv1 rows' part-brightness gates, Support.getSupportScenesOpSet), compared as
  :func:`parse_version` (``BleUtil.parseVersion``). Every gate is a version that parses above 0. Before the
  read, each version not yet read meets its own gates, so the newest firmware's set applies, as 1.x assumed,
  and the Kelvin range is the one valid on every revision. Any other firmware fact or gate raises.
- Light power read-back: a polled light's aa 01 reply is on at a byte of 1
  (``ControllerSwitch.Companion.c``), or at any non-zero byte where the row's wire ``power`` says
  ``light_nonzero`` (the connect read's own decoder: the legacy ``SwitchController``, the H70BX connect
  read) or ``light_heartbeat_nonzero`` (the aa 01 heartbeat's, where the connect read takes == 1: H1168's
  ``HeartControllerWithBattery``). Either becomes ``DeviceProfile.light_nonzero``: as for a plug, the
  heartbeat rule beats the connect read's. On a status or plug row either raises (neither reads a light's
  aa 01 reply), and ``light_heartbeat_nonzero`` raises without the aa 01 heartbeat (``heartbeat: power``).
- Plug power read-back: the wire's ``relay_heartbeat_on_mask`` beats the row's ``relay_on_mask``, because
  every aa 01 poll the library makes follows the heartbeat rule (``power_state.is_on_heartbeat``). So for
  goodsTypes 50 and 307 the row's 0xFF (the connect read's) is never consumed, and the library uses their
  heartbeat 0x03, as 1.x did.
- The fallback cipher, used only when no advertisement says (decision D4): AES-RC4 on an account-locked row
  (the plugs, whose firmware encrypts) and, as the 1.x compatibility rule, on H60A6's row
  (:data:`_AES_FALLBACK_ROWS`: its firmware encrypts, and the spec records no cipher per row,
  ``wire_cipher: runtime``); none on every other row. The family ``encryption`` must be a cipher the library
  implements; it is never read as a control-time lock (D9: rows carry ``account_lock``).
- The account lock (``account_lock``, ``DeviceProfile.requires_secret``): the 0xB2 check with a set secret.
  A row whose screen sends it on every connect, an empty one when the account holds no secret
  (``Controller4Secret.Companion.a("")``: hollowlamp 59, h6071 57 / 67), says ``empty_secret_check``, only
  with ``account_lock`` (``DeviceProfile.empty_secret_check``).
"""
from __future__ import annotations

import re
from collections.abc import Callable, Collection, Iterator, Mapping, Sequence
from typing import Any, Final, NamedTuple, NoReturn, NotRequired, Required, TypedDict


# ── the projected devices.yaml (devices.schema.json definitions) ────────────────────────────────────
class ZoneRow(TypedDict):
    """``clientRow.zones`` item."""
    id: str
    power_index: int
    segments: tuple[int, ...]


class IcPieces(TypedDict):
    """``clientRow.ic_pieces``: the IC count clamped and divided."""
    min_ic: int
    max_ic: int
    ic_per_piece: int
    rounding: NotRequired[str]
    skip_zero: NotRequired[bool]


class IcEntry(TypedDict):
    """``clientRow.ic_pieces.table`` item."""
    ic: int
    pieces: int


class IcTable(TypedDict):
    """``clientRow.ic_pieces``: pieces by the IC count's value."""
    table: tuple[IcEntry, ...]
    otherwise: int
    readback_only: NotRequired[bool]


class IcPair(TypedDict):
    """``clientRow.ic_pieces``: two strips' IC counts, each capped and summed."""
    pair_max: int
    otherwise: int


class HeartbeatReread(TypedDict):
    """``clientRow.heartbeat_reread``: the pieces the screen's keep-alive re-read walks."""
    pieces: int


class LightNumPieces(TypedDict):
    """``clientRow.light_num_pieces``: the aa 0f string count times the pieces per unit."""
    per_unit: int


class Branch(TypedDict, total=False):
    """``clientBranch``: a pact branch of a row."""
    support: str
    mode_color_parse: str
    mode_color_parse_status: str
    status_read: tuple[str, ...]
    capabilities: tuple[str, ...]
    connect_reads: tuple[str, ...]
    scene_versions: tuple[int, ...]
    kelvin_range: tuple[int, ...]
    brightness_range: tuple[int, ...]


class FirmwareBranch(TypedDict, total=False):
    """``clientFirmware``: a firmware branch of a row."""
    min_soft_version: str
    min_hard_version: str
    min_wifi_soft_version: str
    compare: str
    mode_color_parse: str
    scene_versions: tuple[int, ...]
    kelvin_range: tuple[int, ...]
    brightness_range: tuple[int, ...]


class Row(TypedDict, total=False):
    """``clientRow``: one goodsType of ``client_profile.by_goods_type``."""
    models: tuple[str, ...]
    # Not a devices.yaml key: the SKUs only the catalogue snapshot routes to the row, which the generator adds
    # (tools/gen_devices.py GENERATED; :func:`catalogue`), served at :func:`catalogue_support`.
    catalogue_models: tuple[str, ...]
    connectivity: tuple[str, ...]
    pacts: tuple[str, ...] | str
    pact_required: bool
    support: str
    session: str
    power_read: str
    power_write: str
    power_live: tuple[str, ...]
    heartbeat: str
    heartbeat_timing: str
    heartbeat_power: bool
    heartbeat_reread: HeartbeatReread
    mode_color_parse: str
    mode_color_parse_status: str
    relay_power: str
    relay_on_mask: int
    advert_on_mask: int
    color_scheme: str
    white_type: str
    color_temp_control: str
    color_functions: tuple[str, ...]
    capabilities: tuple[str, ...]
    segment_color_temp: bool
    zones: tuple[ZoneRow, ...]
    segment_brightness_groups: tuple[tuple[int, ...], ...]
    account_lock: bool
    empty_secret_check: bool
    scene_dialect: str
    scene_versions: tuple[int, ...]
    kelvin_range: tuple[int, ...]
    brightness_range: tuple[int, ...]
    mode_reports_kelvin: bool
    kelvin_readback: str
    status_read: tuple[str, ...]
    device_info_reads: str
    connect_reads: tuple[str, ...]
    connect_writes: tuple[str, ...]
    power_resync: bool
    ic_pieces: IcPieces | IcTable | IcPair | str
    post_status_reads: tuple[str, ...]
    readback_pieces: str
    light_num_pieces: LightNumPieces
    by_pact: dict[str, Branch]
    by_firmware: tuple[FirmwareBranch, ...]


class ClientModel(TypedDict, total=False):
    """``clientModel``: a per-SKU override."""
    mode_color_parse: str
    mode_color_parse_status: str
    kelvin_range: tuple[int, ...]
    brightness_range: tuple[int, ...]
    scene_power_on: bool
    connect_reads: tuple[str, ...]
    connect_writes: tuple[str, ...]
    ic_pieces: IcPieces | IcTable | IcPair | str
    support: str
    by_pact: dict[str, Branch]
    by_firmware: tuple[FirmwareBranch, ...]


class WireCounts(TypedDict, total=False):
    """``wireCounts``."""
    num_segments: int
    num_levels: int


class WireGroup(TypedDict, total=False):
    """``wireGroup``."""
    rule: str
    num_per_group: int
    num_pieces: int


class WirePower(TypedDict, total=False):
    """``wirePower``."""
    relay_heartbeat_on_mask: int
    light_nonzero: bool
    light_heartbeat_nonzero: bool


class WireMode(TypedDict, total=False):
    """``wireMode``."""
    music: str
    empty_fails: bool


class ModeDecode(TypedDict):
    """A ``wireMode`` with both leaves set: a decode the library reads mode reports with."""
    music: str
    empty_fails: bool


class WireBranch(TypedDict, total=False):
    """``wireBranch``: one override of the govee_profile context."""
    forms: dict[str, str]
    counts: WireCounts
    group: WireGroup
    power: WirePower
    mode: WireMode
    mode_status: WireMode


class WireFirmware(WireBranch, total=False):
    """``wireFirmware``."""
    min_soft_version: str
    min_hard_version: str
    min_wifi_soft_version: str


class WireLayer(WireBranch, total=False):
    """``wireLayer``."""
    by_pact: dict[str, WireBranch]
    by_firmware: tuple[WireFirmware, ...]


class Wire(WireLayer, total=False):
    """``wire``: the family block and its per-SKU and per-goodsType layers."""
    by_goods_type: dict[str, WireLayer]
    by_model: dict[str, WireLayer]


class ClientProfile(TypedDict, total=False):
    """``family.client_profile``."""
    has_brightness: bool
    color_scheme: str
    power_read: str
    power_live: tuple[str, ...]
    mode_color_parse: str
    mode_color_parse_status: str
    relay_power: str
    relay_on_mask: int
    advert_on_mask: int
    by_model: dict[str, ClientModel]
    by_goods_type: dict[str, Row]
    wire: Wire


class Specs(TypedDict, total=False):
    """``family.specs``: the family-wide layered facts."""
    kelvin_range: tuple[int, ...]
    brightness_range: tuple[int, ...]
    scene_versions: tuple[int, ...]


class AssignedCondition(TypedDict, total=False):
    """``assignedGoodsTypeCondition``: the tests on the device record, all of which must hold."""
    min_pact_type: int
    min_pact_code: int
    min_soft_version: str
    min_hard_version: str


class AssignedGoodsType(TypedDict, total=False):
    """``assignedGoodsType``: SKUs of the family whose goodsType the app rewrites itself."""
    models: tuple[str, ...]
    goods_type: int
    from_goods_type: tuple[int, ...]
    condition: AssignedCondition
    pact: str


class Family(TypedDict, total=False):
    """``family``."""
    id: Required[str]
    connectivity: tuple[str, ...]
    specs: Specs
    encryption: str
    assigned_goods_type: tuple[AssignedGoodsType, ...]
    client_profile: ClientProfile


class ModeColorStrategy(TypedDict):
    """``modeColorStrategy``: colour sub-mode byte (``"0x15"``) -> mode_color_decode kind."""
    sub_modes: dict[str, str]


# ── library rules ────────────────────────────────────────────────────────────────────────────────
class SpecError(ValueError):
    """A supported devices.yaml row lacks a fact the library needs, or holds one it can't serve."""


Fail = Callable[[str], NoReturn]

ADMITTED: Final = frozenset({"hardware_verified", "app_traced"})
MODELLED: Final = frozenset({"modelled"})   # admitted only on the caller's opt-in
LEVELS: Final = ADMITTED | MODELLED
CAPABILITIES: Final = ("power", "brightness", "rgb", "color_temp", "segments", "scenes")   # models.Capability
COLOR_SCHEMES: Final = ("h60a6", "h6006", "h6188", "h61a8", "h6061", "legacy_02",
                         "kelvin_v9", "h3010")   # wire.build.ColorScheme
# The h60a6 colour-temperature slot types (clientRow white_type; wire.build.WhiteType), and the default a row
# without one takes (Bytes.r's ff ff ff, every row before 1.8.0).
WHITE_TYPES: Final = ("rgb_black", "rgb_white", "rgb_self_tem_color")
_WHITE_DEFAULT: Final = "rgb_white"
# The schemes whose colour-temperature frame is an illumination-tab Kelvin builder (Config.N[Config.x], with
# the slot it gives each white type): h60a6 = Bytes.C (N[0]), h6188 = Bytes.A (N[2]) and kelvin_v9 = Bytes.D
# (N[9] = JM). The h6006 and h6061 CCT frames (Bytes.p, Bytes.u) are colour-page builders only.
_ILLUMINATION_SCHEMES: Final = frozenset({"h60a6", "h6188", "kelvin_v9"})
# clientRow color_temp_control: how the screen writes colour temperature where it isn't the colour page's
# Kelvin over the selected pieces (absent). Either value gives DeviceProfile.whole_device_kelvin.
COLOR_TEMP_CONTROLS: Final = ("illumination", "whole_device")
# clientRow kelvin_readback: where the read-back's colour temperature comes from where not the mode report's
# Kelvin (absent). pieces gives DeviceProfile.kelvin_from_pieces.
KELVIN_READBACKS: Final = ("pieces",)

# clientRow power_write: the whole-device power write where it isn't the 33 01 main switch (absent):
# light_switch = the H6057 light switch, 33 30 <1 / 0> (DeviceProfile.light_switch_power).
POWER_WRITES: Final = ("light_switch",)
RELAY_POWERS: Final = ("outlet0", "all_outlets", "total_switch")        # devices.profile.RelayPower
HEARTBEAT_TIMINGS: Final = ("fixed_rate", "after_quiet")         # transport.connection.HeartbeatTiming
SESSIONS: Final = ("held", "per_command")                          # devices.profile.SessionMode
# What a per_command row (the device-list card's session) may carry: the card's two writes (DeviceProfile
# checks a hand-built per_command profile against it too).
CARD_CAPABILITIES: Final = frozenset({"power", "brightness"})
# The keep-alive reads a screen sends (clientRow heartbeat, govee_common command ids; DeviceProfile.heartbeat
# takes their codes): ConnectHeartbeat.c's four types (1 aa 01, 2 aa 00, 3 aa 33, 4 aa 36), which cover the
# IHeart controllers' aa 36 / aa 33 and the plug screens' aa 01.
HEARTBEATS: Final = ("power", "heartbeat", "volume", "cmd_36")
# A heartbeat power_live is the reply to the screen's own keep-alive: the command it sends (the cross-check).
_LIVE_HEARTBEATS: Final[Mapping[str, str]] = {
    "heartbeat_aa01": "power", "heartbeat_aa36": "cmd_36", "heartbeat_aa33": "volume"}
# The capabilities a colour scheme has no frame for (wire.build refuses them), which a row's resolution drops
# (only from a scheme the row names itself):
#  - h61a8 (the 0x0b frames): no colour temperature (build.color_temp / segment_color_temp);
#  - h6006 / h6188 (0x0d; h6188 is the white-slot Bytes.A colour temperature of the illumination tab): no
#    segment mask in any colour frame (Bytes.f / p / A);
#  - h6061: whole-device only; per-panel colour needs the user's layout, which is not modelled;
#  - legacy_02 (0x02): Config.fu drops the position set, and Bytes.a / o carry no mask;
#  - kelvin_v9 (15 04 <K>, Bytes.D): a Kelvin only, so no RGB frame (Config.J[9] = hu turns a pick into a
#    Kelvin) and no mask.
#  - h3010 (the 0xA3 colour unit): whole-device only; goodsType 411's Part tab (ColorModeH3010) is sized from
#    the account's segment count and its strip path (ColorModeControllerConfig.k) is untraced.
SCHEME_LACKS: Final[Mapping[str, frozenset[str]]] = {
    "h61a8": frozenset({"color_temp"}),
    "h6006": frozenset({"segments"}),
    "h6188": frozenset({"segments"}),
    "h6061": frozenset({"segments"}),
    "legacy_02": frozenset({"segments"}),
    "kelvin_v9": frozenset({"rgb", "segments"}),
    "h3010": frozenset({"segments"}),
}
# The colour schemes with a segment-brightness frame (wire.build.segment_brightness: 33 05 15 02, Bytes.G),
# the frame the colour page's segment-brightness slider sends; the only rows whose color_functions flag for
# it calls for segments.
SEGMENT_BRIGHTNESS_SCHEMES: Final = frozenset({"h60a6"})
# Wire branches the library leaves unread on purpose: (family, layer, branch) -> the branch exactly.
_UNREAD_WIRE_BRANCHES: Final[Mapping[tuple[str, str, str], Mapping[str, Any]]] = {
    ("h60ax", "by_goods_type 303", "by_pact 1,1"): {"group": {"num_pieces": 1}},
}
# The spec's default mode decode (wireMode), every leaf unset: what a row's wire mode is laid over. A polled
# row's aa 05 replies take the row's own (DeviceProfile.mode_decode, emitted only where it differs from this),
# a status row's TLV 0x05 too (DeviceProfile.status_decode).
_MODE_UNSET: Final[ModeDecode] = {"music": "not_decoded", "empty_fails": False}
# The family encryption tags the library implements: plaintext, the AES-RC4 PSK channel, and the account
# lock, whose cipher is resolved at connect (aes_gcm_v2 is detected at connect but not implemented).
_CIPHERS: Final = frozenset({"none", "aes_v1", "secret_key_pairing"})
# scenes.upload.dialects -> DeviceProfile.scene_dialect; "none" = activate only.
_DIALECTS: Final = {"legacy_scene": "A", "diy_graffiti": "B_h60a6", "tablelamp_h6052_type5_diy": "B_h6052",
                    "none": "A"}
# power_read -> DeviceProfile.readback ("aa34_detail" also sets DeviceProfile.detail_power_read; "advert": no
# GATT read, on a per_command row only).
_READBACK: Final = {"aa01": "polled", "ac_status": "status", "aa34_detail": "polled", "advert": "none"}
# The forms of the TLVs the status stream reads (govee_state's TLV-typed tags; reassemble.status_profile): the
# row's own, in DeviceProfile.status_decode. f_a5 goes in only where the 0xA5 TLV is read as colour groups or
# as the one record (single_record).
_STATUS_TLV_FORMS: Final = ("f_30", "f_36", "f_40", "f_41", "f_a1", "f_a3")
_GROUP_RULES: Final = frozenset({"from_len", "fixed", "by_pieces", "whole_groups"})   # group_count_rule
_READS: Final = frozenset({"brightness", "gradual", "cmd_36", "color_group", "ic_num", "cmd_41",
                           "special_info", "light_num"})
_PACT_FACTS: Final = frozenset({"status_read", "scene_versions", "kelvin_range", "brightness_range"})
# clientRow device_info_reads, a status row's (module doc): the device-info reads are sent but at "none".
_DEVICE_INFO_READS: Final = ("none", "firmware")
# Pact branches whose capabilities the library leaves unread: (family, goodsType, pact key) -> the branch's
# capabilities exactly. D3: H60A6 at pact (1, 1) keeps the row's capabilities.
_UNREAD_PACT_CAPABILITIES: Final[Mapping[tuple[str, str, str], tuple[str, ...]]] = {
    ("h60ax", "303", "1,1"): ("power", "brightness", "rgb", "color_temp", "scenes")}
_AES_FALLBACK_ROWS: Final = frozenset({("h60ax", "303")})   # D4: 1.x's fallback, kept for the unlocked H60A6
# D3, the 1.x pact fallback (module doc): the 18 SKUs 1.4.0 shipped, frozen. profile.profile_for still routes
# one of them to its only admitted profile at a pact no row accepts; every other SKU only at its rows' pacts.
PACT_FALLBACK_SKUS: Final = frozenset({
    "H60A6", "H6047", "H61A8", "H6004", "H6006", "H6008", "H6052", "H6641", "H6188", "H6062", "H6061",
    "H5080", "H5083", "H5085", "H5082", "H5089", "H5160", "H5161"})
_LAYERED: Final = ("scene_versions", "kelvin_range", "brightness_range")
# clientFirmware compare, a Kelvin item's (DeviceProfile.kelvin_version_compare; module doc): "string", the
# default, is KelvinConfig.Config.z2 / Z2 / X2's compareTo; "numeric" is Config.Y2's isHigherVersion.
KELVIN_COMPARES: Final = ("string", "numeric")
# A string-compared gate's form: the app's version strings, with two-digit minor fields ("3.08.01").
_STRING_GATE: Final = re.compile(r"\d+\.\d{2}\.\d{2}")
_PERCENT: Final = (1, 100)
_BLOCKS: Final = ("forms", "counts", "group", "power", "mode", "mode_status")
# Integer.parseInt's form: an optional sign, then decimal digits (Character.digit, radix 10).
_JAVA_INT: Final = re.compile(r"[+-]?\d+")


def parse_version(text: str | None) -> int:
    """A firmware version as the app's numeric compares parse it: the text with its dots removed, as one int
    (``1.06.00`` is 10600, ``1.6.0`` 160), 0 when it is empty or ``Integer.parseInt`` refuses it (no digits,
    another character, past the 32-bit range); as ``govee_reference._parse_version``. That is
    ``BleUtil.parseVersion`` (base-classes9/com/govee/base2home/pact/BleUtil.java:1112-1121;
    ``UtilSku.parseVersion`` and ``NumberUtil.parseVersion`` are the same) and ``BleUtils.q0``
    (base-classes10/com/govee/base2kt/utils/BleUtils.java:1065-1074), the one function under both."""
    digits = (text or "").replace(".", "")
    if not _JAVA_INT.fullmatch(digits):
        return 0
    try:
        value = int(digits)
    except ValueError:   # more digits than int() takes from a str (sys.get_int_max_str_digits)
        return 0
    return value if -(2**31) <= value < 2**31 else 0


def version_key(text: str, compare: str) -> tuple[int, str]:
    """A version's order under ``compare``: a device version meets a gate when its key is at least the
    gate's. ``"numeric"``: :func:`parse_version` (``BleUtils.isHigherVersion`` -> ``Q``,
    base-classes10/com/govee/base2kt/utils/BleUtils.java:327-335, ``q0(device) >= q0(gate)``, inclusive
    through ``isHigherVersion$default``, :74-79; and ``BleUtil.parseVersion``'s gates). ``"string"``: the text
    itself (``KelvinConfig.Config.z2`` / ``Z2`` / ``X2``'s ``compareTo``, :data:`KELVIN_COMPARES`)."""
    return (parse_version(text), "") if compare == "numeric" else (0, text)


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


class Route(NamedTuple):
    """One BLE row's route for one SKU it lists."""
    sku: str
    goods_type: int
    pacts: frozenset[tuple[int, int]] | None
    """The ``(pactType, pactCode)`` pairs the row accepts; None = any (``pacts: any``)."""
    needs_pact: bool
    """The route is taken only on an advertised pact, never without one (the module doc): the row is the
    target goodsType of a pact-conditioned ``assigned_goods_type`` item that names the SKU
    (:func:`pact_gates`), a pairing item with a pact names the SKU (:func:`bind_skus`), or the row is
    ``pact_required``."""


def _layer(*layers: Mapping[str, Any]) -> dict[str, Any]:
    """The wire blocks ``layers`` merged in order, a later layer replacing single leaves."""
    out: dict[str, dict[str, Any]] = {}
    for layer in layers:
        for block in _BLOCKS:
            out.setdefault(block, {}).update(layer.get(block) or {})
    return out


def routes(families: Sequence[Family]) -> dict[str, tuple[Family, str, str]]:
    """``{SKU upper-cased: (family, goodsType, SKU)}`` for each SKU that exactly one BLE row with support
    lists, when that row is admitted: the SKUs routed with neither the pact nor the goodsType. SpecError for
    a row with support (any level) that is no BLE route, is ``pact_required`` with ``pacts: any`` or has a
    pact branch's support it can't route (:func:`pact_support`), for a SKU that a ``modelled`` row and an
    admitted row both list (the spec's ``ble_rows`` counts both wherever both accept the pact, so the opt-in
    would no longer be additive), for a routed SKU that an
    ``assigned_goods_type`` item in any family names (matched as the routes are, case-insensitively) unless
    the item's condition tests the pact alone, and for a row that disagrees with a pact-conditioned item
    (:func:`_reassignments`). The generator runs it over the whole document."""
    listed: dict[str, list[tuple[Family, str, str, bool]]] = {}
    for fam in families:
        rows = (fam.get("client_profile") or {}).get("by_goods_type") or {}
        for gt, row in rows.items():
            if "support" not in row:
                continue
            if any(not isinstance(m, str) or not m for m in row.get("models") or ()):
                raise SpecError(f"{fam['id']} goodsType {gt}: models must be non-empty SKU strings")
            if not _is_route(fam, row):
                raise SpecError(f"{fam['id']} goodsType {gt}: a row with support needs models, pacts and a "
                                "ble link")
            if row.get("pact_required") and isinstance(row.get("pacts"), str):
                raise SpecError(f"{fam['id']} goodsType {gt}: pact_required on a row whose pacts are 'any' "
                                "(no pact to require)")
            pact_support(fam, gt, row)   # its pact branches' support routes (SpecError where it can't)
            for sku in row.get("models") or ():
                admitted = sku_support(fam, row, sku) in ADMITTED
                hits = listed.setdefault(sku.upper(), [])
                other = next((h for h in hits if h[3] != admitted), None)
                if other is not None:
                    raise SpecError(f"{fam['id']} goodsType {gt} ({sku}): also listed by {other[0]['id']} "
                                    f"{other[1]}, a row of the other admission (the modelled opt-in must "
                                    "leave the admitted routes as they are)")
                hits.append((fam, gt, sku, admitted))
    for fam in families:
        for item in fam.get("assigned_goods_type") or ():
            if pact_gate(item) is not None or bind_pact(item) is not None:
                continue   # routing data: _reassignments and _binds check it against the rows
            for sku in item.get("models") or ():
                if isinstance(sku, str) and sku.upper() in listed:
                    owner, gt, routed, _admitted = listed[sku.upper()][0]
                    raise SpecError(f"{owner['id']} goodsType {gt} ({routed}): {fam['id']} "
                                    f"assigned_goods_type reassigns it to goodsType {item.get('goods_type')} "
                                    "(the library follows only a pact-conditioned reassignment or a "
                                    "pairing write's exact pact)")
    _reassignments(families)
    _binds(families)
    return {key: hits[0][:3] for key, hits in listed.items() if len(hits) == 1 and hits[0][3]}


def sku_entries(families: Sequence[Family], routed: Catalogue) -> None:
    """SpecError, in a family with a row with support, for a ``by_model`` or ``wire.by_model`` entry of a
    SKU that no row of the family lists and that ``routed`` (:func:`catalogue` over the same ``families``)
    routes to none of them (H6009 / H6010's ``scene_power_on`` are such catalogue routes). The generator
    runs it over the whole document, since its cut keeps only the routed SKUs' entries and would drop a
    stray one silently."""
    for fam in families:
        cp = fam.get("client_profile") or {}
        rows = cp.get("by_goods_type") or {}
        if any("support" in row for row in rows.values()):
            own = {m.upper() for row in rows.values() for m in row.get("models") or () if isinstance(m, str)}
            own.update(sku for (fam_id, _gt), skus in routed.added.items() if fam_id == fam["id"]
                       for sku in skus)
            for layer, entries in (("by_model", cp.get("by_model") or {}),
                                   ("wire by_model", (cp.get("wire") or {}).get("by_model") or {})):
                unlisted = sorted(sku for sku in entries if sku.upper() not in own)
                if unlisted:
                    raise SpecError(f"{fam['id']} {layer} {unlisted}: no row of the family lists them (a "
                                    "per-SKU entry needs the SKU's row or catalogue route)")


def pact_gate(item: AssignedGoodsType) -> tuple[int, int] | None:
    """A reassignment's ``(min_pact_type, min_pact_code)`` when its condition tests the pact alone, else None
    (a firmware gate, or none: the library can't follow those)."""
    cond = item.get("condition") or {}
    pact_type, code = cond.get("min_pact_type"), cond.get("min_pact_code")
    if set(cond) != {"min_pact_type", "min_pact_code"} or pact_type is None or code is None:
        return None
    return int(pact_type), int(code)


def bind_pact(item: AssignedGoodsType) -> tuple[int, int] | None:
    """A pairing (``bind``) item's ``pact`` as ``(pactType, pactCode)``: the advertised pact at which, and
    only at which, the app writes the item's goodsType into the record it creates
    (``NewRgb2TLUtil.isNewRgb2TL`` / ``isNewRgb2TL4BleIot`` compare the pact exactly). None for a device-list
    item (a ``condition`` or a ``from_goods_type``) and for an item without a pact."""
    if "pact" not in item or "condition" in item or "from_goods_type" in item:
        return None
    return _pact(item["pact"])


def _binds(families: Sequence[Family]) -> list[str]:
    """Check each pairing item with a pact (:func:`bind_pact`) against the rows, and give the (SKU, pact)
    routes no row carries yet, one review line each. At that pact the app runs the SKU at the item's
    goodsType, so a row of another goodsType that lists the SKU must not accept the pact (``any`` accepts
    every pact): SpecError. A row at the item's goodsType that lists the SKU and accepts the pact carries the
    route; one that lists it without ``pacts`` is for review, as is a route no row carries."""
    review: list[str] = []
    for fam in families:
        for item in fam.get("assigned_goods_type") or ():
            pact, target = bind_pact(item), item.get("goods_type")
            if pact is None:
                continue
            rule = f"{fam['id']} pairing write, goodsType {target} at exactly pact {pact[0]},{pact[1]}"
            for sku in item.get("models") or ():
                if not isinstance(sku, str):
                    continue
                carried = False
                for owner in families:
                    for gt, row in ((owner.get("client_profile") or {}).get("by_goods_type") or {}).items():
                        listed = {m.upper() for m in row.get("models") or () if isinstance(m, str)}
                        if sku.upper() not in listed:
                            continue
                        if "pacts" not in row:
                            if int(gt) == target:
                                review.append(f"{sku}: also listed by {owner['id']} goodsType {gt}, without "
                                              f"pacts ({rule})")
                            continue
                        pacts = row["pacts"]
                        accepts = isinstance(pacts, str) or any(_pact(p) == pact for p in pacts)
                        if accepts and int(gt) != target:
                            raise SpecError(f"{owner['id']} goodsType {gt} ({sku}): pacts {pacts} accept "
                                            f"the pact of the {rule}, where the app runs it at goodsType "
                                            f"{target}")
                        carried = carried or accepts
                if not carried:
                    review.append(f"{sku}: no goodsType-{target} row with pact {pact[0]},{pact[1]} ({rule})")
    return review


def bind_review(families: Sequence[Family]) -> tuple[str, ...]:
    """The (SKU, pact) routes of the pairing items with a pact that no row of ``families`` (the whole
    document) carries yet, one line each, for the generator to print (:func:`_binds`)."""
    return tuple(_binds(families))


def bind_skus(families: Sequence[Family]) -> frozenset[str]:
    """The SKUs (upper-cased) that a pairing item with a pact (:func:`bind_pact`) of ``families`` names: each
    of their routes needs an advertised pact (``Route.needs_pact``, the module doc)."""
    return frozenset(sku.upper() for fam in families for item in fam.get("assigned_goods_type") or ()
                     if bind_pact(item) is not None
                     for sku in item.get("models") or () if isinstance(sku, str))


def pact_gates(families: Sequence[Family]) -> dict[str, dict[int, tuple[int, int]]]:
    """``{SKU upper-cased: {target goodsType: gate}}`` for each pact-conditioned ``assigned_goods_type`` item
    of ``families`` (:func:`pact_gate`), for each SKU it names: the target routes :func:`profiles` marks
    ``Route.needs_pact``."""
    gates: dict[str, dict[int, tuple[int, int]]] = {}
    for fam in families:
        for item in fam.get("assigned_goods_type") or ():
            gate, target = pact_gate(item), item.get("goods_type")
            if gate is None or target is None:
                continue
            for sku in item.get("models") or ():
                if isinstance(sku, str):
                    gates.setdefault(sku.upper(), {})[int(target)] = gate
    return gates


def _meets(pact: tuple[int, int], gate: tuple[int, int]) -> bool:
    """The record test of ``assignedGoodsTypeCondition``: pactType and pactCode each at or above the
    gate's."""
    return pact[0] >= gate[0] and pact[1] >= gate[1]


def _reassignments(families: Sequence[Family]) -> list[str]:
    """Check each pact-conditioned ``assigned_goods_type`` item against the rows, and give the routes no row
    carries yet, one review line each. A row that lists one of its SKUs with ``pacts`` at its ``goods_type``
    must accept only pacts at or above the gate, and one at a ``from_goods_type`` only pacts below it
    (``any`` is neither), so the advertised pact picks the row the app's rewrite picks; a row at another
    goodsType is neither checked nor refused. SpecError where a row disagrees. A row at either goodsType
    that lists the SKU without ``pacts`` is for review too: once it has pacts, both rows carry the route."""
    review: list[str] = []
    for fam in families:
        for item in fam.get("assigned_goods_type") or ():
            gate = pact_gate(item)
            if gate is None:
                continue
            target, sources = item.get("goods_type"), tuple(item.get("from_goods_type") or ())
            rule = f"{fam['id']} assigned_goods_type, {list(sources)} -> {target} at pacts >= {gate}"
            for sku in item.get("models") or ():
                if not isinstance(sku, str):
                    continue
                carried: set[int] = set()
                for owner in families:
                    for gt, row in ((owner.get("client_profile") or {}).get("by_goods_type") or {}).items():
                        at = int(gt)
                        if (at != target and at not in sources) or sku.upper() not in {
                                m.upper() for m in row.get("models") or () if isinstance(m, str)}:
                            continue
                        if "pacts" not in row:
                            review.append(f"{sku}: also listed by {owner['id']} goodsType {gt}, without "
                                          f"pacts ({rule})")
                            continue
                        pacts = row["pacts"]
                        side = at == target
                        if isinstance(pacts, str) or any(_meets(_pact(p), gate) != side for p in pacts):
                            raise SpecError(f"{owner['id']} goodsType {gt} ({sku}): pacts {pacts} not all "
                                            f"{'at or above' if side else 'below'} the gate of {rule}")
                        carried.add(at)
                review += [f"{sku}: no goodsType-{g} row with pacts "
                           f"{'at or above' if g == target else 'below'} the gate ({rule})"
                           for g in (target, *sources) if g not in carried]
    return review


def reassignment_review(families: Sequence[Family]) -> tuple[str, ...]:
    """The routes of the pact-conditioned ``assigned_goods_type`` items that no row of ``families`` (the whole
    document) carries yet, one line each, for the generator to print (:func:`_reassignments`)."""
    return tuple(_reassignments(families))


def _rewrites(item: AssignedGoodsType, code: int) -> bool:
    """An ``assigned_goods_type`` item cites the app's rewrite of catalogue goodsType ``code``: its
    ``from_goods_type`` names it, or, with none (from any goodsType), its condition tests the pact. A
    firmware-gated item from any goodsType, or a pairing write (which applies at its own pact alone), cites
    none."""
    sources = item.get("from_goods_type")
    return code in sources if sources is not None else pact_gate(item) is not None


def _is_route(fam: Family, row: Row) -> bool:
    """A row is a BLE route: it lists models (or the generator gave it catalogue routes) and pacts, and ble is
    in its (else its family's) connectivity."""
    return bool(row.get("models") or row.get("catalogue_models")) and "pacts" in row and "ble" in (
        row.get("connectivity") or fam.get("connectivity") or ())


def sku_support(fam: Family, row: Row, sku: str) -> str:
    """The support level of ``sku`` on ``row`` of ``fam``, a SKU the row lists: its ``by_model`` ``support``
    where it has one (a SKU whose own fact is inferred where the row's others are traced: h6609 260's
    IC-gated read-back), else the row's ("" for none)."""
    model = ((fam.get("client_profile") or {}).get("by_model") or {}).get(sku) or {}
    return model.get("support", row.get("support", ""))


def pact_support(fam: Family, gt: str, row: Row) -> dict[str, str]:
    """``{pact key: support level}`` for each of ``row``'s pact branches that has its own ``support``, the
    row's level at that pact (module doc). SpecError where the row's pacts are ``any`` (no pact to route a
    branch at), or where a SKU of the row has a ``by_model`` ``support`` too."""
    levels = {key: branch["support"] for key, branch in (row.get("by_pact") or {}).items()
              if "support" in branch}
    if levels:
        where = f"{fam['id']} goodsType {gt}"
        if isinstance(row.get("pacts"), str):
            raise SpecError(f"{where}: a pact branch's support on a row whose pacts are 'any'")
        models = (fam.get("client_profile") or {}).get("by_model") or {}
        if any("support" in (models.get(sku) or {}) for sku in row.get("models") or ()):
            raise SpecError(f"{where}: a pact branch's support beside a by_model support")
    return levels


def catalogue_support(level: str) -> str:
    """The support level of a SKU only the catalogue snapshot routes to a row at ``level``: the row's, capped
    at ``app_traced``."""
    return "app_traced" if level == "hardware_verified" else level


class Catalogue(NamedTuple):
    """The catalogue snapshot's routes over devices.yaml (:func:`catalogue`)."""
    added: dict[tuple[str, str], tuple[str, ...]]
    """``{(family id, goodsType): SKUs}``: the SKUs no row lists that the snapshot routes to that row."""
    unrouted: frozenset[str]
    """Upper-cased SKUs that a row with support lists at a goodsType the snapshot contradicts, with no cited
    rewrite: the spec's route doesn't win, so no row routes them."""
    review: tuple[str, ...]
    """One line per SKU the snapshot leaves for review, which the generator prints."""


def catalogue(families: Sequence[Family], snapshot: Mapping[str, int]) -> Catalogue:
    """The routes ``snapshot`` (``spec/govee_catalog.json``: SKU -> catalogue goodsType) adds to
    devices.yaml's ``families`` (the whole document), with SKUs compared upper-cased as :func:`routes` does.

    A SKU that some client_profile row lists keeps the spec's goodsType. Its catalogue goodsType is a conflict
    when it is non-zero and none of those rows has it. An ``assigned_goods_type`` item of a listing row's
    family that names the SKU at that row's goodsType settles it when the item is the app's cited rewrite of
    that catalogue goodsType (:func:`_rewrites`; :func:`routes` checks or refuses that item on a routed SKU).
    Any other item, such as a pairing write (at its own pact alone), settles nothing. An unsettled conflict is
    for review, and a row with support no longer routes the SKU. A SKU no row lists routes to the BLE row
    with support at its catalogue goodsType when that is the only row at it, but never at 0 (the legacy code,
    not H6188's goodsType-0 row); an ``assigned_goods_type`` item naming it (the app rewrites its goodsType),
    or rows of several families at that goodsType, leave it for review."""
    listed: dict[str, list[tuple[Family, str, Row]]] = {}
    rows_at: dict[int, list[tuple[Family, str, Row]]] = {}
    for fam in families:
        for gt, row in ((fam.get("client_profile") or {}).get("by_goods_type") or {}).items():
            for sku in row.get("models") or ():
                if isinstance(sku, str):
                    listed.setdefault(sku.upper(), []).append((fam, gt, row))
            rows_at.setdefault(int(gt), []).append((fam, gt, row))
    rewrites: dict[str, list[tuple[str, AssignedGoodsType]]] = {}
    for fam in families:
        for item in fam.get("assigned_goods_type") or ():
            for sku in item.get("models") or ():
                if isinstance(sku, str):
                    rewrites.setdefault(sku.upper(), []).append((fam["id"], item))
    added: dict[tuple[str, str], list[str]] = {}
    unrouted: set[str] = set()
    review: list[str] = []
    for sku, code in sorted(snapshot.items()):
        key = sku.upper()
        if key in listed:
            here = {(fam["id"], int(g)) for fam, g, _row in listed[key]}
            if code == 0 or code in {g for _f, g in here} or any(
                    (owner, item.get("goods_type")) in here and _rewrites(item, code)
                    for owner, item in rewrites.get(key, ())):
                continue
            rows = ", ".join(f"{fam['id']} {g}" for fam, g, _row in listed[key])
            routed = any("support" in row for _fam, _g, row in listed[key])
            review.append(f"{key}: catalogue goodsType {code}, devices.yaml {rows}, no cited rewrite"
                          + ("; unrouted" if routed else ""))
            if routed:
                unrouted.add(key)
        elif code != 0 and any("support" in row and _is_route(fam, row)
                               for fam, _g, row in rows_at.get(code, ())):
            hits = rows_at[code]
            if key in rewrites:
                review.append(f"{key}: catalogue goodsType {code}, rewritten by an assigned_goods_type item")
            elif len(hits) > 1:
                review.append(f"{key}: catalogue goodsType {code} has rows in "
                              + ", ".join(fam["id"] for fam, _g, _row in hits))
            else:
                added.setdefault((hits[0][0]["id"], hits[0][1]), []).append(key)
    return Catalogue({k: tuple(v) for k, v in added.items()}, frozenset(unrouted), tuple(review))


def _members(families: Sequence[Family], admitted: Collection[str]
             ) -> Iterator[tuple[Family, str, Row, str, frozenset[tuple[int, int]] | None, bool]]:
    """``(family, goodsType, row, SKU, accepted, pact-less)`` for each SKU a BLE row routes at a support level
    in ``admitted``, in devices.yaml order: the row's models at their level, then its catalogue routes at
    :func:`catalogue_support`. ``accepted`` is the row's pacts at a level in ``admitted`` (a pact branch's
    ``support`` replacing the level at its pact, :func:`pact_support`; None for ``pacts: any``), never empty;
    ``pact-less``, whether the SKU's own level is in ``admitted``, so that the route serves no pact."""
    for fam in families:
        for gt, row in ((fam.get("client_profile") or {}).get("by_goods_type") or {}).items():
            if not _is_route(fam, row):
                continue
            branches = pact_support(fam, gt, row)
            pacts = row.get("pacts", "any")   # every route has pacts (_is_route); 'any' tests none
            cap = catalogue_support
            for sku, via in (*((sku, False) for sku in row.get("models") or ()),
                             *((sku, True) for sku in row.get("catalogue_models") or ())):
                level = cap(row.get("support", "")) if via else sku_support(fam, row, sku)
                own = level in admitted
                if isinstance(pacts, str):
                    if own:
                        yield fam, gt, row, sku, None, True
                    continue
                at = frozenset(_pact(p) for p in pacts
                               if (cap(branches[p]) if via and p in branches else branches.get(p, level))
                               in admitted)
                if at:
                    yield fam, gt, row, sku, at, own


def profiles(families: Sequence[Family], parse_names: Collection[str], admitted: Collection[str] = ADMITTED,
             catalogued: Collection[str] | None = None
             ) -> list[tuple[tuple[str, ...], dict[str, Any], tuple[Route, ...]]]:
    """``[(skus, fields, routes)]``: DeviceProfile field values for each group of (row, SKU) routes at a
    support level in ``admitted`` (a catalogue route's is :func:`catalogue_support`) whose resolved facts are
    identical, in devices.yaml order (row models, then its catalogue routes, within a row), with those routes
    (``Route.needs_pact`` on a route at a pact-conditioned item's target goodsType, :func:`pact_gates`, on
    each route of a SKU a pairing item with a pact names, :func:`bind_skus`, on a ``pact_required`` row's
    routes, and on a route that only a pact branch's ``support`` admits, :func:`pact_support`).
    ``parse_names`` are the mode_color_parse table's names; ``catalogued`` are the SKUs (upper-case) with a
    bundled scene catalog, the only ones offered scenes (None: no cut). SpecError on a fact missing or not
    served."""
    groups: dict[str, tuple[list[str], dict[str, Any], list[Route]]] = {}
    gates, paired = pact_gates(families), bind_skus(families)
    for fam, gt, row, sku, accepted, pactless in _members(families, admitted):
        needs_pact = (not pactless   # a route only a pact branch's support admits (pact_support)
                      or bool(row.get("pact_required"))   # never with pacts 'any' (routes)
                      or sku.upper() in paired or int(gt) in gates.get(sku.upper(), {}))
        fields = _fields(fam, gt, sku, parse_names, catalogued)
        skus, _same, members = groups.setdefault(repr(sorted(fields.items())), ([], fields, []))
        if sku not in skus:
            skus.append(sku)
        members.append(Route(sku, int(gt), accepted, needs_pact))
    return [(tuple(skus), fields, tuple(members)) for skus, fields, members in groups.values()]


# The by_model facts that replace a row's for the SKU (clientModel), on the one row of its family that lists
# it.
_ROW_OVERRIDES: Final = ("connect_reads", "connect_writes", "ic_pieces", "support")


def _fields(fam: Family, gt: str, sku: str, parse_names: Collection[str],
            catalogued: Collection[str] | None = None) -> dict[str, Any]:
    """One routed SKU's DeviceProfile field values (see the module doc for the rules)."""
    cp = fam.get("client_profile") or {}
    row = (cp.get("by_goods_type") or {})[gt]
    model = (cp.get("by_model") or {}).get(sku) or {}
    where = f"{fam['id']} goodsType {gt} ({sku})"

    def fail(problem: str) -> NoReturn:
        raise SpecError(f"{where}: {problem}")

    def need(value: Any, name: str) -> Any:
        if value is None:
            fail(f"no {name}")
        return value

    w = _wire(fam, gt, sku, fail)
    forms, counts, group, power = w["forms"], w["counts"], w["group"], w["power"]
    relay = forms.get("f_01") == "relays"
    if model.get("by_pact") or any(k not in ("kelvin_range", "compare") and not k.startswith("min_")
                                   for item in model.get("by_firmware") or () for k in item):
        fail("by_model pact / firmware branches are not served (but a firmware kelvin_range)")
    per_row = sorted(k for k in _ROW_OVERRIDES if k in model)
    if per_row:   # the SKU's own, on the one row of the family that lists it
        listing = [g for g, r in (cp.get("by_goods_type") or {}).items() if "support" in r
                   and sku.upper() in {m.upper() for m in r.get("models") or () if isinstance(m, str)}]
        if len(listing) != 1:
            fail(f"by_model {per_row} on a SKU that rows {listing} list (the screen splits them by SKU "
                 "within one goodsType: one row must list it)")
    if fam.get("encryption") not in _CIPHERS:
        fail(f"encryption {fam.get('encryption')!r} is not a cipher the library implements")

    # Layered facts: the row's own (its pact and firmware branches resolve apart), else the SKU's by_model
    # entry, else the family specs.
    own: dict[str, object] = dict(row)
    mine: dict[str, object] = dict(model)
    specs: dict[str, object] = dict(fam.get("specs") or {})
    shadowed = sorted(k for k in _LAYERED if k in own and k in mine)
    if shadowed:
        fail(f"by_model {shadowed} under the row's own (the row's wins)")
    layered: dict[str, Any] = {k: own[k] if k in own else mine[k] if k in mine else specs[k]
                               for k in _LAYERED if k in own or k in mine or k in specs}
    row_kelvin, scenes_by_firmware = _firmware(row, fail)
    model_kelvin, _no_scenes = _firmware(model, fail)
    kelvin_fw = row_kelvin or model_kelvin
    if ((row_kelvin and model_kelvin) or (model_kelvin and "kelvin_range" not in mine)
            or len({gate for gate, *_entry in kelvin_fw}) > 1):
        fail("Kelvin firmware branches must be the row's, or the SKU's by_model ones over its own "
             "kelvin_range, on one version")
    compares = sorted({compare for *_entry, compare in kelvin_fw})
    if len(compares) > 1:
        fail(f"Kelvin firmware branches on compares {compares} (a row's items are on one compare)")
    kelvin_compare = compares[0] if compares else "string"
    kelvin_by_version = sorted(((at, lo, hi) for _gate, at, lo, hi, _compare in kelvin_fw),
                               key=lambda entry: version_key(entry[0], kelvin_compare), reverse=True)
    by_soft = any(gate == "min_soft_version" for gate, *_entry in kelvin_fw)

    status_read = row.get("status_read")
    by_pact = row.get("by_pact") or {}
    listed_pacts = row.get("pacts")
    stray = sorted(set(by_pact) - set(listed_pacts)) if isinstance(listed_pacts, tuple) else []
    if stray:
        fail(f"pact branches {stray} are not among the row's pacts {list(listed_pacts or ())} (no device at "
             "them reaches the row)")
    for key, branch in by_pact.items():
        pinned = _UNREAD_PACT_CAPABILITIES.get((fam["id"], gt, key))
        unread = ({"capabilities"} if pinned is not None and tuple(branch.get("capabilities") or ()) == pinned
                  else set())
        facts = set(branch) - unread - {"support"}   # a pact's support level routes (_members), no fact
        if facts - _PACT_FACTS:
            fail(f"pact branch {key} on {sorted(facts - _PACT_FACTS)} is not served")
    scenes_by_pact = tuple(sorted((_pact(key), _scene_set(branch["scene_versions"], fail))
                                  for key, branch in by_pact.items() if "scene_versions" in branch))
    gate: tuple[int, int] | None = None
    status_by_pact: tuple[tuple[tuple[int, int], tuple[str, ...]], ...] = ()
    if status_read is not None:
        if len(set(status_read)) != len(status_read):
            fail(f"status_read {list(status_read)} (distinct TLV types)")
        gate, status_by_pact = _status_branches(row, sku, tuple(status_read), fail)
    # Every read the row's status requests make, at any pact: the forms and the colour-group decode serve
    # each.
    status_reads = (tuple(status_read), *(read for _key, read in status_by_pact)) if status_read is not None \
        else ()

    row_caps = frozenset(need(row.get("capabilities"), "capabilities"))
    if not row_caps <= {*CAPABILITIES, "gradual"}:
        fail(f"capabilities {sorted(row_caps - {*CAPABILITIES, 'gradual'})}")
    session = row.get("session", "held")
    if session not in SESSIONS:
        fail(f"session {session!r} (one of {list(SESSIONS)})")
    card = session == "per_command"
    if card:
        # The device-list card's session (timing.per_command): its one frame, no GATT read, no connect write,
        # no keep-alive, and only the card's 33 01 / 33 04. The rest is not served on it yet.
        lives_here = row.get("power_live", cp.get("power_live")) or ()
        for what, there in (
                ("a relay row (the card's 33 01 is the light switch)", relay),
                (f"power_read {row.get('power_read', cp.get('power_read'))!r} (no GATT read: advert only)",
                 not relay and row.get("power_read", cp.get("power_read")) != "advert"),
                (f"heartbeat {row.get('heartbeat')!r} (no link is held: none only)",
                 row.get("heartbeat") != "none"),
                ("a heartbeat_timing", "heartbeat_timing" in row),
                (f"connect_reads {list(row.get('connect_reads') or ())}", bool(row.get("connect_reads"))),
                (f"connect_writes {list(row.get('connect_writes') or ())}", bool(row.get("connect_writes"))),
                ("a status_read", status_read is not None),
                (f"power_live {list(lives_here)}", bool(lives_here)),
                ("account_lock true (the card sends no 33 b2: AbsDeviceModel4BleIot.A0 is null)",
                 bool(row.get("account_lock"))),
                ("zones", bool(row.get("zones"))),
                ("ic_pieces", "ic_pieces" in row),
                ("power_resync", bool(row.get("power_resync"))),
                (f"capabilities {sorted(row_caps - CARD_CAPABILITIES)} (the card sends only 33 01 and 33 04)",
                 not row_caps <= CARD_CAPABILITIES)):
            if there:
                fail(f"{what} on a per_command row is not served yet")
    caps = set(row_caps)
    scheme = row.get("color_scheme") or cp.get("color_scheme")
    if row_caps & {"rgb", "color_temp", "segments"}:
        if relay:
            fail(f"capabilities {sorted(row_caps & {'rgb', 'color_temp', 'segments'})} on a relay row (the "
                 "library resolves no colour scheme or Kelvin range for a plug)")
        need(scheme, "color_scheme")
    if scheme is not None and scheme not in COLOR_SCHEMES:
        fail(f"color_scheme {scheme!r} is not one the library builds")
    white = row.get("white_type", _WHITE_DEFAULT)
    if white not in WHITE_TYPES:
        fail(f"white_type {white!r} (one of {list(WHITE_TYPES)})")
    if white != _WHITE_DEFAULT and scheme != "h60a6":
        fail(f"white_type {white} on the {scheme} scheme (the white slot is the h60a6 frame's, Bytes.r)")
    lacks = caps & SCHEME_LACKS.get(scheme or "", frozenset())
    if lacks and "color_scheme" not in row:
        fail(f"the family color_scheme {scheme} would drop {sorted(lacks)}: the row must name it")
    caps -= lacks
    control = row.get("color_temp_control")
    part_kelvin = row.get("segment_color_temp")
    cf = row.get("color_functions")
    # The colour page's flags decide color_temp only where the colour page sets it: not under the
    # illumination tab's control.
    page_kelvin = control != "illumination"
    if cf is not None and ((page_kelvin and ("color_temperature" in cf) != ("color_temp" in row_caps))
                           or (scheme in SEGMENT_BRIGHTNESS_SCHEMES
                               and "segment_brightness" in cf and "segments" not in row_caps)):
        fail(f"color_functions {list(cf)} disagree with capabilities {sorted(row_caps)}")
    dialect = row.get("scene_dialect")
    if "scenes" in caps and dialect not in (*_DIALECTS, "untraced"):
        fail(f"scene_dialect {dialect!r}")
    if dialect == "untraced" or (catalogued is not None and sku.upper() not in catalogued):
        caps.discard("scenes")
    uploads = "scenes" in caps and dialect != "none"
    scenes = _scene_set(need(layered.get("scene_versions"), "scene_versions"), fail) if uploads else ()
    if "color_temp" in caps:
        need(layered.get("kelvin_range"), "kelvin_range")
    # Every scheme with a colour-temperature mode sends an RGB pick that is a Kelvin tint colour in the range
    # as colour temperature, without testing the screen's support for it (Config.Yt / bu / Zt / fu:
    # KelvinConfig.i; build.rgb_as_kelvin), so its rows need the range for rgb alone too.
    if ("rgb" in caps and "color_temp" not in SCHEME_LACKS.get(scheme or "", frozenset())
            and layered.get("kelvin_range") is None):
        fail(f"no kelvin_range: a {scheme} row with rgb needs one, as its colour write sends a pick that "
             "is a Kelvin tint colour in the range as colour temperature (KelvinConfig.i)")
    kelvin = (_kelvin(layered["kelvin_range"], "kelvin_range", fail)
              if layered.get("kelvin_range") is not None and not relay else None)
    _check_kelvin_by_firmware(kelvin, kelvin_by_version, kelvin_compare, fail)
    kelvin_by_pact = tuple(sorted(
        [(_pact(key), *_kelvin(branch["kelvin_range"], f"pact {key} kelvin_range", fail))
         for key, branch in by_pact.items() if "kelvin_range" in branch], key=lambda e: e[0]))
    if kelvin_by_pact and (kelvin is None or kelvin_fw):
        fail("a pact kelvin_range needs a base kelvin_range (the row's, the SKU's by_model or the family's) "
             "and no Kelvin firmware branch")
    if kelvin is not None and not overlaps_every(kelvin, [(lo, hi) for _key, lo, hi in kelvin_by_pact]):
        fail(f"pact kelvin_ranges {[list(e[1:]) for e in kelvin_by_pact]} must each overlap the base "
             f"{list(kelvin)}, and so must the range valid on every pact (used until the pact is heard)")
    bright = need(layered.get("brightness_range"), "brightness_range") if "brightness" in caps else None
    raw_bright = _brightness(bright, "brightness_range", fail) if bright is not None else None
    bright_by_pact = tuple(sorted(
        [(_pact(key), _brightness(branch["brightness_range"], f"pact {key} brightness_range", fail))
         for key, branch in by_pact.items() if "brightness_range" in branch], key=lambda e: e[0]))
    if bright_by_pact and bright is None:
        fail("a pact brightness_range without the brightness capability")
    if control is not None and control not in COLOR_TEMP_CONTROLS:
        fail(f"color_temp_control {control!r} (one of {list(COLOR_TEMP_CONTROLS)}, or absent)")
    if control == "whole_device" and "color_temp" not in caps:
        fail("color_temp_control whole_device needs the color_temp capability")
    if control == "illumination":
        if not {"color_temp", "brightness"} <= caps:
            fail("color_temp_control illumination needs the color_temp and brightness capabilities (the tab "
                 "writes the Kelvin and the brightness)")
        if raw_bright is not None or bright_by_pact:
            fail("color_temp_control illumination needs percent brightness, no pact branch (33 04 re-sends "
                 "the held value)")
        if scheme not in _ILLUMINATION_SCHEMES or white == "rgb_self_tem_color":
            fail(f"color_temp_control illumination on {scheme} / {white} (the tab's Kelvin builder is the "
                 "h60a6, h6188 or kelvin_v9 frame, the h60a6 slot ff ff ff for rgb_self_tem_color)")

    readback, power_read = "plug", ""
    if not relay:
        power_read = need(row.get("power_read", cp.get("power_read")), "power_read")
        if power_read not in _READBACK:
            fail(f"power_read {power_read!r}")
        if power_read == "advert" and not card:
            fail("power_read 'advert' on a held row is not served (the library reads the power on a held "
                 "link)")
        readback = _READBACK[power_read]
        if (readback == "status") != (status_read is not None):
            fail(f"power_read {power_read!r} with status_read {status_read!r}")
    info_reads = row.get("device_info_reads")
    if (info_reads is None) == (readback == "status"):
        fail(f"device_info_reads {info_reads!r} on a {readback} row (each status row names it, no other)")
    if info_reads is not None and info_reads not in _DEVICE_INFO_READS:
        fail(f"device_info_reads {info_reads!r} (one of {list(_DEVICE_INFO_READS)})")
    reads = set(need(model.get("connect_reads", row.get("connect_reads")), "connect_reads"))
    if not reads <= _READS:
        fail(f"connect_reads {sorted(reads - _READS)} are not served")
    if ("brightness" in reads) != (readback == "polled" and "brightness" in caps):
        fail("connect_reads lists brightness exactly on a polled row with the brightness capability (the "
             "library reads aa 04 on every poll of one)")
    if "gradual" in caps and "gradual" not in reads:
        fail("connect_reads lists gradual with the capability (the library reads aa a3 for it)")
    if "gradual" in reads and "gradual" not in caps and readback != "polled":
        fail("a gradual read without the capability is served on a polled light row only (the screens' "
             "default Compose4InfoBleIot read)")
    color_readback = ""
    if "color_group" in reads:
        color_readback = {"color_group": "mechanism_a_direct", "bulb_batch_v2": "mechanism_b"}.get(
            forms.get("f_a5", ""), "")
        if not color_readback:
            fail(f"colour read-back with f_a5 {forms.get('f_a5')!r}")
    # The status read takes colour groups from TLV 0xA5 where it requests them, or on the bare read (whose
    # screen parses each 0xA5 that arrives, VM4H70DX.g5), and the row reads them so.
    status_groups = (any("color_group" in read or not read for read in status_reads)
                     and forms.get("f_a5") == "color_group")
    if (color_readback or status_groups) and cp.get("has_brightness") is not True:
        fail("a colour-group read without has_brightness true (the library reads [brightness, r, g, b])")
    # The one-record TLV 0xA5 (H703A / H703B): the status read's, and SubMode4Color.l takes it at one piece
    # only.
    single_record = forms.get("f_a5") == "single_record"
    if single_record and (status_read is None or "color_group" not in status_read):
        fail("f_a5 single_record without color_group in the status read (it is the status TLV 0xA5's form)")
    if single_record and counts.get("num_segments") != 1:
        fail(f"f_a5 single_record over wire counts num_segments {counts.get('num_segments')!r} "
             "(SubMode4Color.l takes its one record at one piece only)")
    # Either read walks the row's group size (Controller4ColorInfoByGroup takes it from its caller), and the
    # status read counts each group's records by the row's rule.
    per_group = group.get("num_per_group")
    if (color_readback == "mechanism_a_direct" or status_groups) \
            and not (_is_int(per_group) and per_group >= 1):
        fail(f"wire group num_per_group {per_group!r} (the colour groups are read in groups of it)")
    if status_groups and group.get("rule") not in _GROUP_RULES:
        fail(f"wire group rule {group.get('rule')!r} (the status read counts its colour groups' records by "
             "it)")
    ic = model.get("ic_pieces", row.get("ic_pieces"))   # the SKU's rule where its screen sizes it by SKU
    post = row.get("post_status_reads")
    if post is not None and (status_read is None or set(post) != {"ic_num"}):
        fail(f"post_status_reads {list(post)} (an aa 40 read after a status read, ic_num, is served)")
    around = ("ic_num" in reads, "ic_num" in (post or ()))   # the aa 40 reads around the status read
    tlv_ic = status_read is not None and "ic_num" in status_read and ic is not None
    if status_read is None:
        if ("ic_num" in reads) != (ic is not None) or (
                ic is not None and color_readback != "mechanism_a_direct"):
            fail("an ic_num read and ic_pieces go together, with the direct colour read-back")
    elif any(around) != (ic is not None and not tlv_ic) or isinstance(ic, str) or (tlv_ic and any(around)):
        fail("ic_pieces on a status row: an object form with one IC source, the status read's TLV 0x40 "
             "(status_read ic_num) or aa 40 reads around it (connect_reads / post_status_reads ic_num), "
             "which need it")
    if tlv_ic and forms.get("f_40") not in ("one_count", "one_count_u16"):
        fail(f"ic_pieces from the status read's TLV 0x40 with f_40 {forms.get('f_40')!r} (one_count or "
             "one_count_u16: the one-count readers)")
    if isinstance(ic, str) and ic not in ("fixed", "sections"):
        fail(f"ic_pieces {ic!r} (fixed, sections, or an IC rule)")
    # An object form: a rule over the live IC count (H6641's clamp and divisor, the h705a floor, H6609's
    # table, H1A45's pair).
    ic_rule = {} if ic is None or isinstance(ic, str) else _ic_rule(ic, fail)
    live_ic = bool(ic_rule)
    if live_ic and not tlv_ic and forms.get("f_40") == "ic_pair":   # ControllerIcNum.e reads the aa 40 reply
        ic_rule["color_readback_ic_pair_reader"] = True
    elif "color_readback_ic_pair_cap" in ic_rule:
        fail("ic_pieces pair_max without wire f_40 ic_pair on an aa 40 read (the pair is the IC-pair "
             "reader's)")
    readback_ic = row.get("readback_pieces")
    if readback_ic is not None and (readback_ic != "ic_count" or not status_groups or not live_ic):
        fail(f"readback_pieces {readback_ic!r} (ic_count, on a status row with an ic_pieces object form that "
             "reads colour groups)")
    section_count = "cmd_41" in reads
    if section_count and forms.get("f_41") != "section_count":
        fail("an aa 41 read without f_41 section_count")
    special_info = "special_info" in reads
    if special_info and (section_count or readback != "polled" or "segments" not in row_caps
                         or isinstance(ic, dict) or ic == "sections"):
        fail("the special_info segment-count read on a polled row with segments only, without the aa 41 "
             "section count or an IC-sized read-back (one source sizes the masks and the walk)")
    piece_kelvin = row.get("kelvin_readback")
    # The fold counts the static or device-reported pieces (an IC-sized walk counts others) and takes the
    # mode from the same poll's aa 05 report (_fold.apply_piece_kelvin).
    if piece_kelvin is not None and (piece_kelvin not in KELVIN_READBACKS or "color_temp" not in caps
                                     or color_readback != "mechanism_a_direct" or readback != "polled"
                                     or live_ic or ic == "sections"):
        fail(f"kelvin_readback {piece_kelvin!r} (one of {list(KELVIN_READBACKS)}, with color_temp and the "
             "direct colour read-back whose pieces it reads, on a polled row whose walk is not IC-sized)")
    # The aa 0f string count times the pieces per unit (h7020 NewDetailVM.u5): the row's one live count.
    light_num = row.get("light_num_pieces")
    if ("light_num" in reads) != (light_num is not None):
        fail("an aa 0f light_num read and light_num_pieces go together")
    per_unit = 0 if light_num is None else light_num["per_unit"]
    if light_num is not None:
        if readback != "polled":
            fail("an aa 0f light_num read is served on a polled light row only (the h7020 bulb-string "
                 "screens' aa-read connect)")
        if live_ic or section_count or special_info:
            fail("light_num_pieces with another live piece count (an ic_pieces object form, the aa 41 "
                 "section count or the special_info read)")
        if not (_is_int(per_unit) and per_unit >= 1):
            fail(f"light_num_pieces per_unit {per_unit!r} (1 or more)")
    # The H707A status screen's TLV 0xA1: its segment count sizes every mask (H707AInfo4Detail.G0).
    segment_info = status_read is not None and "cmd_a1" in status_read
    if segment_info != (forms.get("f_a1") == "segment_info"):
        fail("a requested cmd_a1 and wire f_a1 segment_info go together (the status read's segment count)")
    bar = forms.get("f_36") == "bar_pair"
    if ("cmd_36" in reads) != bar:
        fail("the aa 36 read and f_36 bar_pair go together")
    if power_read == "aa34_detail" and bar:
        fail("power_read aa34_detail on a bar row (the bars' power is the aa 36 read's)")
    power_write = row.get("power_write")
    if power_write is not None and power_write not in POWER_WRITES:
        fail(f"power_write {power_write!r} (one of {list(POWER_WRITES)}, or absent)")
    if (power_write == "light_switch") != (power_read == "aa34_detail"):
        fail("power_write light_switch and power_read aa34_detail go together (the H6057 light switch and "
             "its light flag, the screen's on / off)")
    writes = tuple(need(model.get("connect_writes", row.get("connect_writes")), "connect_writes"))
    if writes not in ((("plug_sync_time",),) if relay else ((), ("light_effect_stop",))):
        fail(f"connect_writes {list(writes)} (the library sends the plug time sync on every relay row, and "
             "light_effect_stop or nothing on a light)")
    resync = row.get("power_resync")
    if relay:
        need(resync, "power_resync")
    elif resync:
        fail("power_resync on a light (the library re-syncs a plug's session only)")
    live_served = {"heartbeat_aa01": power_read in ("aa01", "ac_status"), "heartbeat_aa36": bar,
                   "push_30": forms.get("f_ee_30", "unknown") != "unknown"}
    lives = row.get("power_live", cp.get("power_live")) or ()
    for live in lives:
        if not live_served.get(live, False):
            fail(f"power_live {live} is not served (the library polls aa 01 or the status read's TLV 0x01, "
                 "the aa 36 bars, and an ee 30 push needs its wire f_ee_30)")
    heartbeat = need(row.get("heartbeat"), "heartbeat")
    if heartbeat == "none" and not card:
        fail("heartbeat none on a held row is not served (a held link sends the screen's keep-alive)")
    timing = row.get("heartbeat_timing", "fixed_rate")
    if timing not in HEARTBEAT_TIMINGS:
        fail(f"heartbeat_timing {timing!r} (one of {list(HEARTBEAT_TIMINGS)})")
    # One way only: a heartbeat power_live, or a plug's relay (its aa 01 heartbeat reply), names the read the
    # screen sends; nothing in the app ties a push_30-only row to aa 00.
    sent = {_LIVE_HEARTBEATS[live] for live in lives if live in _LIVE_HEARTBEATS}
    if relay:
        sent.add("power")
    if sent and sent != {heartbeat}:
        fail(f"heartbeat {heartbeat} where power_live {list(lives)}{' and the relay' if relay else ''} take "
             f"power from the reply to {' / '.join(sorted(sent))}")
    # The row's opt-in: the status read once per link, then the power from each aa 01 keep-alive reply
    # (DeviceProfile.heartbeat_power), which is the whole device's one switch (ShareVM.N3 -> J3). Its
    # heartbeat is then power (the check above).
    beat_power = bool(row.get("heartbeat_power", False))
    if beat_power and not (readback == "status" and "heartbeat_aa01" in lives and not row.get("zones")):
        fail("heartbeat_power is served only on an unzoned status row whose power_live has heartbeat_aa01 "
             "(the keep-alive reply is the one switch)")
    # The screen's re-read on a keep-alive power change (J3 -> Compose4Off2On: aa 05, then its aa a5
    # groups): the pieces it walks, in groups of the wire group's num_per_group
    # (DeviceProfile.heartbeat_reread_pieces).
    reread = row.get("heartbeat_reread")
    reread_pieces = 0
    if reread is not None:
        walked = reread.get("pieces") if isinstance(reread, dict) else None
        if not beat_power:
            fail("heartbeat_reread without heartbeat_power (the re-read follows a keep-alive reply's change)")
        if not (_is_int(walked) and walked >= 1):
            fail(f"heartbeat_reread pieces {walked!r} (1 or more: the pieces the screen's aa a5 groups walk)")
        if not (_is_int(per_group) and per_group >= 1):
            fail("heartbeat_reread needs the wire group num_per_group (the size of its aa a5 groups)")
        reread_pieces = int(walked)
    _check_mode(w, readback, fail)
    # A light's aa 01 rule, as a plug's: the heartbeat's beats the connect read's (module doc).
    nonzero = [name for name in ("light_heartbeat_nonzero", "light_nonzero") if power.get(name, False)]
    if nonzero and readback != "polled":
        fail(f"wire power {nonzero[0]} is served on a polled light row only (the aa 01 reply the library "
             "reads on every poll)")
    if "light_heartbeat_nonzero" in nonzero and heartbeat != "power":
        fail(f"wire power light_heartbeat_nonzero with heartbeat {heartbeat} (it is the aa 01 heartbeat's "
             "rule)")

    zones = tuple((z["id"], z["power_index"], tuple(z["segments"])) for z in need(row.get("zones"), "zones"))
    if power_write and zones:
        fail(f"power_write {power_write} on a zoned row (it is the whole-device frame, no zone's)")
    groups = tuple(tuple(g) for g in row.get("segment_brightness_groups") or ())
    lock = bool(need(row.get("account_lock"), "account_lock"))
    empty_check = bool(row.get("empty_secret_check", False))
    if empty_check and not lock:
        fail("empty_secret_check without account_lock (the empty check is the account lock's 33 b2)")
    out: dict[str, Any] = {
        "capabilities": tuple(c for c in CAPABILITIES if c in caps),
        "segment_color_temp": part_kelvin is not False,   # True, the default, where it means nothing
        "aes_fallback": lock or (fam["id"], gt) in _AES_FALLBACK_ROWS,
        "segments": int(counts.get("num_segments", 0)),
        "zones": zones,
        "segment_brightness_groups": groups,
        "scene_versions": scenes,
        "scene_versions_by_firmware": tuple(scenes_by_firmware) if uploads else (),
        "scene_versions_by_pact": scenes_by_pact if uploads else (),
        "scene_dialect": _DIALECTS.get(dialect or "none", "A"),
        "min_kelvin": kelvin[0] if kelvin else None,
        "max_kelvin": kelvin[1] if kelvin else None,
        "requires_secret": lock,
        "relay": relay,
        "bar_switch": bar,
        "global_power": bool(zones) and not bar and forms.get("f_01") == "light",
        "gradual": "gradual" in caps,
        "gradual_read_only": "gradual" in reads and "gradual" not in caps,
        "color_readback": color_readback,
        "color_readback_per_batch": (int(per_group) if color_readback == "mechanism_a_direct" or reread_pieces
                                     else 3),
        "color_readback_live_ic": live_ic,
        # the live pieces size the writes too (ColorPieceConfig.c), unless the rule sizes the walk alone
        "color_writes_live_ic": live_ic and not ic_rule.get("color_readback_ic_walk_only", False),
        "color_readback_ic_sections": ic == "sections",
        "detail_power_read": power_read == "aa34_detail",
        "light_switch_power": power_write == "light_switch",
        "ic_num_read_only": ic == "fixed",
        "section_count_read": section_count,
        "special_info_read": special_info,
        "status_segment_count": segment_info,
        "brightness_range": raw_bright,
        "kelvin_by_pact": kelvin_by_pact,
        "brightness_by_pact": bright_by_pact,
        "status_color_groups_pact": gate,
        "power_resync": bool(resync),
        "kelvin_by_hardware": () if by_soft else tuple(kelvin_by_version),   # highest version first
        "kelvin_by_software": tuple(kelvin_by_version) if by_soft else (),
        "kelvin_version_compare": kelvin_compare,
        "mode_reports_kelvin": bool(row.get("mode_reports_kelvin", True)),
        "device_info_reads": info_reads != "none",
        "light_effect_stop": writes == ("light_effect_stop",),
        "scene_power_on": bool(model.get("scene_power_on", False)),
        "heartbeat": heartbeat,    # a command name (or "none"); devices/profile.py takes its code
        "heartbeat_timing": timing,
        "light_nonzero": bool(nonzero),
        "session": session,
    }
    if out["scene_power_on"] and "scenes" not in row_caps:
        fail("by_model scene_power_on on a row without the scenes capability")
    # Before SCHEME_LACKS (h6061); a live string count's num_segments is the count before its first read.
    if ("segments" in row_caps or color_readback or per_unit) and not out["segments"]:
        fail("segments or a colour read-back without wire counts num_segments")
    if per_unit:
        out["light_num_per_unit"] = per_unit
    for name, _index, segs in zones:
        if not all(0 <= s < out["segments"] for s in segs):
            fail(f"zone {name} segments {list(segs)} are not all below the {out['segments']} segments")
    if groups and ("segments" not in caps or scheme not in SEGMENT_BRIGHTNESS_SCHEMES):
        fail("segment_brightness_groups without the segment-brightness frame (segments on an h60a6 row)")
    pieces = [i for g in groups for i in g]
    if len(set(pieces)) != len(pieces) or not all(0 <= i < out["segments"] for i in pieces):
        fail(f"segment_brightness_groups {[list(g) for g in groups]} are not disjoint groups of the "
             f"{out['segments']} segments")
    if scheme is not None:
        out["color_scheme"] = scheme
    if white != _WHITE_DEFAULT:
        out["white_type"] = white
    if control is not None:   # neither the tab nor a whole_device page writes a Kelvin on part of the device
        out["whole_device_kelvin"] = True
    # Segment brightness (15 02): the scheme's frame and the colour page's slider (untraced: the 1.x rule).
    if "segments" in caps and not (scheme in SEGMENT_BRIGHTNESS_SCHEMES
                                   and (cf is None or "segment_brightness" in cf)):
        out["segment_brightness"] = False
    if control == "illumination":
        out["kelvin_with_brightness"] = True
    if piece_kelvin is not None:
        out["kelvin_from_pieces"] = True
    if beat_power:
        out["heartbeat_power"] = True
    if empty_check:
        out["empty_secret_check"] = True
    if reread_pieces:
        out["heartbeat_reread_pieces"] = reread_pieces
    if status_read is not None:
        out["status_read"] = tuple(status_read)   # command names; devices/profile.py takes their codes
        if status_by_pact:
            out["status_read_by_pact"] = status_by_pact
        decode: dict[str, Any] = {f: forms[f] for f in _STATUS_TLV_FORMS
                                  if forms.get(f, "unknown") != "unknown"}
        if status_groups:
            decode.update(f_a5="color_group", group_rule=group["rule"], num_per_group=int(per_group),
                          has_brightness=True)
        elif single_record:
            decode.update(f_a5="single_record")
        decode.update({**_MODE_UNSET, **w["mode"], **w["mode_status"]})   # the TLV 0x05's
        out["status_decode"] = tuple(sorted(decode.items()))
    if readback == "polled":
        mode = {**_MODE_UNSET, **w["mode"]}   # the aa 05 reply's (_check_mode: no mode_status)
        if mode != _MODE_UNSET:
            out["mode_decode"] = tuple(sorted(mode.items()))
    out.update(ic_rule)
    if status_read is not None and any(around):
        out["ic_reads_around_status"] = around
    if readback_ic is not None:
        out["color_readback_ic_raw"] = True
    if relay:
        relay_power = need(row.get("relay_power", cp.get("relay_power")), "relay_power")
        if relay_power not in RELAY_POWERS:
            fail(f"relay_power {relay_power!r} is not a frame the library builds")
        on_mask = power.get("relay_heartbeat_on_mask") or row.get("relay_on_mask", cp.get("relay_on_mask"))
        advert = row.get("advert_on_mask", cp.get("advert_on_mask"))
        masks = {"relay_on_mask": need(on_mask, "relay_on_mask"),
                 "advert_on_mask": need(advert, "advert_on_mask")}
        for name, mask in masks.items():
            if not (_is_int(mask) and 1 <= mask <= 0xFF):
                fail(f"{name} {mask!r} is not a mask within a byte")
        out.update(readback="plug", relay_power=relay_power, **masks)
    else:
        out["readback"] = readback
        out["mode_color_parse"] = need(
            row.get("mode_color_parse", model.get("mode_color_parse", cp.get("mode_color_parse"))),
            "mode_color_parse")
        status = row.get("mode_color_parse_status",
                         model.get("mode_color_parse_status", cp.get("mode_color_parse_status")))
        if status is not None:
            out["mode_color_parse_status"] = status
        if "untraced" in {out["mode_color_parse"], status} and caps & {"rgb", "color_temp"}:
            fail("mode_color_parse untraced")
        unknown = {out["mode_color_parse"], status or out["mode_color_parse"]} - set(parse_names)
        if unknown:
            fail(f"mode_color_parse {sorted(unknown)} not in the mode_color_parse table")
    _check_forms(forms, _forms_used(out, caps, tuple(t for read in status_reads for t in read), relay), fail)
    if ({"segments", "color_temp"} <= caps) != (part_kelvin is not None):
        fail("segment_color_temp goes with segments and color_temp (the colour scheme's drops applied): "
             "whether the screen sends a Kelvin over a piece selection")
    if control is not None and part_kelvin:   # whole_device_kelvin: no Kelvin on part of the device
        fail(f"segment_color_temp true with color_temp_control {control} (its Kelvin covers every piece)")
    return out


# clientRow ic_pieces rounding (DeviceProfile.color_readback_ic_rounding).
IC_ROUNDINGS: Final = ("ceil", "floor")


def _ic_rule(ic: IcPieces | IcTable | IcPair, fail: Fail) -> dict[str, Any]:
    """An object form of ``ic_pieces`` as the DeviceProfile fields that hold its rule over the live IC count:
    the clamp, divisor, (where it is not ``ceil``) rounding and (where set) ``skip_zero``; or the ``(IC,
    pieces)`` table; or the pair cap; the last two with the pieces ``otherwise``."""
    rule: Mapping[str, Any] = ic
    if "table" in rule:
        table = tuple((entry["ic"], entry["pieces"]) for entry in rule["table"])
        if (not table or len({at for at, _n in table}) != len(table)
                or not all(at >= 0 and n >= 1 for at, n in table) or rule["otherwise"] < 1
                or not isinstance(rule.get("readback_only", False), bool)):
            fail(f"ic_pieces {dict(rule)} (distinct ics >= 0, pieces and otherwise >= 1, readback_only a "
                 "boolean)")
        walk_only = {"color_readback_ic_walk_only": True} if rule.get("readback_only", False) else {}
        return {"color_readback_ic_table": table, "color_readback_ic_otherwise": rule["otherwise"],
                **walk_only}
    if "pair_max" in rule:
        if rule["pair_max"] < 1 or rule["otherwise"] < 1:
            fail(f"ic_pieces {dict(rule)} (pair_max and otherwise >= 1)")
        return {"color_readback_ic_pair_cap": rule["pair_max"],
                "color_readback_ic_otherwise": rule["otherwise"]}
    rounding, skip_zero = rule.get("rounding", "ceil"), rule.get("skip_zero", False)
    if not (0 <= rule["min_ic"] <= rule["max_ic"] and rule["ic_per_piece"] >= 1 and rounding in IC_ROUNDINGS
            and isinstance(skip_zero, bool)):
        fail(f"ic_pieces {dict(rule)} (min_ic <= max_ic, ic_per_piece >= 1, rounding one of "
             f"{list(IC_ROUNDINGS)}, skip_zero a boolean)")
    out: dict[str, Any] = {"color_readback_ic_range": (rule["min_ic"], rule["max_ic"]),
                           "color_readback_ic_per_piece": rule["ic_per_piece"]}
    if rounding != "ceil":
        out["color_readback_ic_rounding"] = rounding
    if skip_zero:   # VM4H608A.S4: an IC of 0 stores no count
        out["color_readback_ic_skip_zero"] = True
    return out


def _wire(fam: Family, gt: str, sku: str, fail: Fail) -> dict[str, Any]:
    """The SKU's wire facts: the family ``wire``, its ``by_model.<SKU>`` and ``by_goods_type.<gt>`` merged.
    A pact or firmware branch in any of them fails (the library reads the wire with both unknown), but for
    an unread one of :data:`_UNREAD_WIRE_BRANCHES`."""
    wire = (fam.get("client_profile") or {}).get("wire") or {}
    layers: dict[str, WireLayer] = {
        "": wire, f"by_model {sku}": (wire.get("by_model") or {}).get(sku) or {},
        f"by_goods_type {gt}": (wire.get("by_goods_type") or {}).get(gt) or {}}
    for name, layer in layers.items():
        branches: list[tuple[str, Mapping[str, Any]]] = [
            *((f"by_pact {k}", b) for k, b in (layer.get("by_pact") or {}).items()),
            *((f"by_firmware {i}", b) for i, b in enumerate(layer.get("by_firmware") or ()))]
        for key, branch in branches:
            if _UNREAD_WIRE_BRANCHES.get((fam["id"], name, key)) != branch:
                label = " ".join(filter(None, ("wire", name, key)))
                fail(f"{label} is not served (the library reads the wire with the pact and firmware unknown)")
    return _layer(*layers.values())


def _check_mode(w: Mapping[str, Any], readback: str, fail: Fail) -> None:
    """The row's wire ``mode`` / ``mode_status`` must be a decode the library reads its mode reports with: on
    a status row any (the TLV 0x05 takes the row's own, mode_status over mode, ``status_decode``), on a
    polled row any ``mode`` (the aa 05 reply takes it, ``mode_decode``) and no mode_status (it has no status
    read), none on a plug or a per_command row (it reads no mode report)."""
    if readback == "status":
        return
    served = not w["mode_status"] and (readback == "polled" or not w["mode"])
    if not served:
        fail(f"wire mode {w['mode']} / mode_status {w['mode_status']} is not a decode the library reads with")


def _forms_used(out: Mapping[str, Any], caps: Collection[str], status_read: Sequence[str],
                relay: bool) -> list[tuple[str, str]]:
    """``[(command form, the member the library sends or reads)]`` for one resolved row: the power frame, a
    plug's total switch, the colour scheme's frames, and the zone switch and the zone bits the status read
    takes from TLV 0x30, and the aa 34 device detail a power_read aa34_detail row reads (the colour read-back,
    aa 41 and aa 36 forms are checked where they are derived)."""
    used = [("f_01", "relays" if relay else "light")]
    if out.get("detail_power_read"):
        used.append(("f_34", "device_detail"))
    if out.get("light_switch_power"):
        used.append(("f_30", "light_switch"))
    if relay and out.get("relay_power") == "total_switch":
        used.append(("f_02", "total_switch"))
    scheme = out.get("color_scheme")
    if scheme == "h60a6" and set(caps) & {"rgb", "color_temp", "segments"}:
        used.append(("f_15_01", "kelvin_tint"))
    if scheme == "h6061" and set(caps) & {"rgb", "color_temp"}:
        used.append(("f_15_01", "kelvin"))
    if scheme == "h61a8" and set(caps) & {"rgb", "segments"}:
        used.append(("f_0b", "rgb_mask"))
    if scheme == "kelvin_v9" and "color_temp" in caps:
        used.append(("f_15_04", "kelvin_v9"))
    if (out["zones"] and not out["bar_switch"]) or "cmd_30" in status_read:
        used.append(("f_30", "zone_power"))
    return used


def _check_forms(forms: Mapping[str, str], used: Sequence[tuple[str, str]], fail: Fail) -> None:
    """Every command form the library sends or reads for the row is the row's (wire ``forms``)."""
    for form, member in used:
        if forms.get(form) != member:
            fail(f"wire forms {form} {forms.get(form)!r}, where the library uses {member}")


def _firmware(row: Row | ClientModel, fail: Fail) -> tuple[list[tuple[str, str, int, int, str]],
                                                           list[tuple[str, str, tuple[int, ...]]]]:
    """The row's (or a by_model entry's) ``by_firmware`` items as Kelvin entries, ``(gate, version, min,
    max, compare)`` with the gate ``min_hard_version`` or ``min_soft_version`` and the item's ``compare``
    (:data:`KELVIN_COMPARES`, ``string`` by default), and ``scene_versions_by_firmware`` entries."""
    kelvin: list[tuple[str, str, int, int, str]] = []
    scenes: list[tuple[str, str, tuple[int, ...]]] = []
    for item in row.get("by_firmware") or ():
        gates: dict[str, Any] = {k: v for k, v in item.items() if k.startswith("min_")}
        facts: dict[str, Any] = {k: v for k, v in item.items() if not k.startswith("min_") and k != "compare"}
        if not all(isinstance(v, str) and parse_version(v) > 0 for v in gates.values()):
            fail(f"firmware gates {gates} are not all a.bb.cc versions")
        if "kelvin_range" in facts:
            compare = item.get("compare", "string")
            if len(facts) != 1 or set(gates) not in ({"min_hard_version"}, {"min_soft_version"}):
                fail("a Kelvin firmware branch must gate on min_hard_version or min_soft_version alone")
            if compare not in KELVIN_COMPARES:
                fail(f"Kelvin firmware compare {compare!r} (one of {list(KELVIN_COMPARES)})")
            (gate, at), = gates.items()
            if compare == "string" and not _STRING_GATE.fullmatch(at):
                fail(f"Kelvin firmware gate {at!r} is not the app's a.bb.cc form (two-digit fields: the gate "
                     "compares as a string)")
            kelvin.append((gate, at, *_kelvin(facts["kelvin_range"], "firmware kelvin_range", fail), compare))
        elif "compare" in item:
            fail(f"firmware compare on {sorted(facts)}: only a Kelvin branch chooses its compare (the others "
                 "compare as BleUtil.parseVersion)")
        elif set(facts) == {"scene_versions"}:
            if not gates or not set(gates) <= {"min_soft_version", "min_hard_version"}:
                fail("a scene firmware branch must gate on the soft / hard version")
            scenes.append((gates.get("min_soft_version", ""), gates.get("min_hard_version", ""),
                           _scene_set(facts["scene_versions"], fail)))
        else:
            fail(f"firmware branch on {sorted(facts)} is not served")
    return kelvin, scenes


def _kelvin(value: Any, name: str, fail: Fail) -> tuple[int, int]:
    """A ``[min, max]`` Kelvin range as a tuple; ``fail`` unless it is two ints, 0 < min <= max."""
    if not (len(value) == 2 and all(map(_is_int, value)) and 0 < value[0] <= value[1]):
        fail(f"{name} {list(value)} is not a [min, max] Kelvin range")
    return int(value[0]), int(value[1])


def _check_kelvin_by_firmware(base: tuple[int, int] | None, gated: Sequence[tuple[str, int, int]],
                              compare: str, fail: Fail) -> None:
    """``kelvin_by_hardware`` / ``kelvin_by_software``'s rules (``DeviceProfile._check_kelvin_by_version``):
    a base range, one entry per version (under ``compare``: :func:`version_key`), each overlapping the base,
    and a range valid on every revision."""
    if not gated:
        return
    lo, hi = base if base is not None else (0, -1)
    if (base is None or len({version_key(v, compare) for v, _lo, _hi in gated}) != len(gated)
            or any(k_lo > hi or k_hi < lo for _v, k_lo, k_hi in gated)
            or max(lo, *(e[1] for e in gated)) > min(hi, *(e[2] for e in gated))):
        fail(f"Kelvin firmware branches {list(gated)} need a base kelvin_range {base}, one per version, "
             "each overlapping it and a range valid on every revision")


def overlaps_every(base: tuple[int, int], ranges: Sequence[tuple[int, int]]) -> bool:
    """Each of ``ranges`` overlaps the ``base`` range, and the base cut to all of them is not empty: the
    range valid on every pact (or revision) the library uses until it is known."""
    lo, hi = base
    return (all(r_lo <= hi and r_hi >= lo for r_lo, r_hi in ranges)
            and max([lo, *(r[0] for r in ranges)]) <= min([hi, *(r[1] for r in ranges)]))


def _brightness(value: Any, name: str, fail: Fail) -> tuple[int, int] | None:
    """A raw ``[min, max]`` brightness range as a tuple, None for percent (``[1, 100]``); ``fail`` unless
    100 % maps into a byte: NumberUtil.calculateProgress(100) = min + 99 * (max - min + 3) / 100."""
    if not (len(value) == 2 and all(map(_is_int, value)) and 0 <= value[0] < value[1] <= 0xFF
            and value[0] + 99 * value[1] + 297 < 0x100 * 100):
        fail(f"{name} {list(value)} is not a raw [min, max] that 100 % maps into a byte")
    return None if tuple(value) == _PERCENT else (int(value[0]), int(value[1]))


def _scene_set(value: Any, fail: Fail) -> tuple[int, ...]:
    """A scene versionArray, sorted; ``fail`` unless every version is a non-negative int."""
    if not all(_is_int(v) and v >= 0 for v in value):
        fail(f"scene_versions {list(value)} are not all non-negative ints")
    return tuple(sorted(value))


def _pact(key: str) -> tuple[int, int]:
    """A ``"pactType,pactCode"`` key (devices.schema.json pattern) as ints."""
    pact_type, code = (int(v) for v in key.split(","))
    return pact_type, code


def _status_branches(row: Row, sku: str, status_read: tuple[str, ...], fail: Fail
                     ) -> tuple[tuple[int, int] | None,
                                tuple[tuple[tuple[int, int], tuple[str, ...]], ...]]:
    """``(status_color_groups_pact, status_read_by_pact)`` from the row's pact branches' ``status_read``.
    Where each branch's read is the row's, or it without ``color_group``, the first is the gate
    (:func:`_status_gate`) and the second empty: H60A6's, the one gate form. Otherwise no gate, and every
    branch whose read differs from the row's is its own exact-pact read, ``((pact type, code), names)`` in
    pact order, which ``Device`` sends at that advertised pact (the spec's ``client_row``; the row's own read
    at any other pact and before one is heard), as the H7070 screen adds 0x42 at (1, 2) / (2, 1)
    (``VM4LightH7070.T4``, ``Support.supportEffectChange``). ``fail`` on a branch with a repeated type, and on
    a branch of that kind on a SKU of the 1.x pact fallback (:data:`PACT_FALLBACK_SKUS`): a pact no row lists
    still routes such a SKU to the row, and there the gate decides its read, as in 1.x (D3)."""
    short = tuple(t for t in status_read if t != "color_group")
    branches = {key: tuple(branch["status_read"])
                for key, branch in (row.get("by_pact") or {}).items() if "status_read" in branch}
    for key, read in branches.items():
        if len(set(read)) != len(read):
            fail(f"pact {key} status_read {list(read)} (distinct TLV types)")
    if all(read in (status_read, short) for read in branches.values()):
        return _status_gate(row, status_read, branches, fail), ()
    if sku.upper() in PACT_FALLBACK_SKUS:
        fail("a pact's status_read is the row's with more than color_group dropped, on a SKU of the 1.x pact "
             "fallback (D3: its colour-group gate decides the read at a pact no row lists)")
    return None, tuple(sorted((_pact(key), read) for key, read in branches.items() if read != status_read))


def _status_gate(row: Row, status_read: tuple[str, ...], branches: Mapping[str, tuple[str, ...]],
                 fail: Fail) -> tuple[int, int] | None:
    """``status_color_groups_pact`` from the row's pact ``branches``, each the row's ``status_read`` or it
    without ``color_group``: None when every pact's status read asks for the 0xA5 colour groups, else the
    ``(pact type, lowest code)`` of the pacts that do. ``fail`` unless the pacts that keep the groups are one
    pact type from that code up (the gate the library implements), each within a byte."""
    without = {k for k, read in branches.items() if read != status_read}
    if not without:
        return None
    pacts = row.get("pacts")
    if not isinstance(pacts, tuple):
        fail("a status_read pact branch needs listed pacts")
    keep = sorted(_pact(p) for p in pacts if p not in without)
    drop = [_pact(p) for p in without]
    if not keep or len({t for t, _ in keep}) != 1:
        fail("the pacts with colour groups are not one pact type")
    pact_type, code = keep[0]
    if any(t == pact_type and c >= code for t, c in drop):
        fail("the pacts with colour groups are not a code range")
    if not (0 <= pact_type <= 0xFF and 0 <= code <= 0xFF):
        fail(f"the colour-group pact ({pact_type}, {code}) is not within a byte")
    return pact_type, code
