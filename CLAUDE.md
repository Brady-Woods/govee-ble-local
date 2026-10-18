# govee-ble-local: rules for Claude

`CONTRIBUTING.md` is the full contributor guide (gates, branching, commits, releases). This file
holds the rules that have been broken before and must not be broken again.

## Home Assistant dependency rules (never break these)

This library runs inside Home Assistant (via `hass-govee-ble-local`). A dependency spec that
conflicts with Home Assistant's own pins makes the integration fail to load for every user
("Not loaded", requirement install fails). That happened in 1.2.0, issue #8: `cryptography<49`
against Home Assistant 2026.10's `cryptography==50.0.1`.

1. **Runtime dependencies get a `>=` floor and nothing else.** No `<`, `<=`, `==`, `~=` or `!=` on
   anything in `[project].dependencies`. `tests/test_dependency_policy.py` enforces this.
   Home Assistant installs integration requirements with
   `uv pip install --constraint homeassistant/package_constraints.txt`
   (`homeassistant/requirements.py`, `homeassistant/util/package.py`), so inside Home Assistant
   its pin always wins. An upper bound here can't protect anything; it only breaks once Home
   Assistant moves past it.
2. **Never raise a floor above what the oldest supported Home Assistant ships.** That is the
   `homeassistant` version in `hass-govee-ble-local/hacs.json`. Check that tag's
   `homeassistant/package_constraints.txt` before raising a floor on `bleak`,
   `bleak-retry-connector` or `cryptography`.
3. **No new runtime dependency** without explicit approval from the user. The allowed set is
   `bleak`, `bleak-retry-connector`, `cryptography` (all shipped with Home Assistant) and
   `kaitaistruct`.
4. **Don't keep a ceiling to quiet a tool.** If `pip-audit` or Dependabot flags something, fix it
   by resolving to a fixed version, not by pinning below the advisory or ignoring it.
5. **Before releasing any change to dependencies, prove it installs alongside Home Assistant.**
   Build the wheel and resolve it against both the current stable and the `dev`
   `package_constraints.txt`:

   ```bash
   gh api -H "Accept: application/vnd.github.raw" \
     "repos/home-assistant/core/contents/homeassistant/package_constraints.txt?ref=<stable tag or dev>" \
     > ha_constraints.txt
   python -m pip install --dry-run -c ha_constraints.txt dist/govee_ble_local-<version>-py3-none-any.whl
   ```

   A `ResolutionImpossible` here means the release would break Home Assistant users. Don't
   ship it.

If a dependency genuinely seems to need an upper bound (a known-breaking upstream API change),
stop and ask the user. Don't add one on your own judgement.

## Match the Govee app (never break this)

The library re-implements what the official Govee Home app sends to each device. Its wire
behaviour must match the app's for that SKU: frames, read sequences and value ranges. On top of
that it supports what the firmware or hardware is shown to do beyond the app (rule 6).

1. **Send exactly what the app sends.** That means the same byte layout, frame count, order and
   values, even when hardware is known to accept something else. Example: the H60A6 works with a
   zero colour-temperature tint, but the app sends the tint, so the library sends it too (#19).
2. **Scope features to what the app does.** Don't send frames the app never sends, such as an
   all-bits mask where the app uses group frames, or values it can't produce, such as a kelvin
   off its 100 K tint table. The only exception is a firmware-confirmed extension (rule 6).
3. **When the library and the app disagree, the library is wrong.** Fix it to match, even if
   the current behaviour works on hardware. Verify on hardware where possible.
4. **Trace the path the app actually runs for that goodsType**: model, then detail screen,
   then view model, then builder. A lookalike class in another package doesn't count. Cite
   `Class.method` for every protocol claim (CONTRIBUTING: the decompiled app is the source of
   truth).
5. **If the app has more than one path** (for example the control screen vs automations or
   group control), match the control-screen path and document the other.
6. **The app is the standard, but support what the firmware adds.** When the decompiled firmware
   or the hardware shows functionality the app doesn't use, support it in the spec and in the
   library. Rules 1-5 still win wherever the app covers the same thing: an extension adds what
   the app lacks and never changes a frame the app sends. Cite the firmware handler (image and
   function) or the hardware observation, and mark it as firmware-sourced in the spec and the
   code. Example: the app sends the `aa 07 02` device-id read only while pairing
   (`SnController`), but the firmware answers it, so the library reads it with the other
   device-info reads.

If matching the app looks impossible or harmful, stop and ask the user. Don't diverge on your
own judgement.

## The spec is the bible (never break this)

The decompiled app and firmware are the ground truth (the app wins wherever it covers a
behaviour, as above). The spec in `spec/` is the single canonical model of them: the `.ksy`
files above all, then `devices.yaml` and its schema. `spec/*.py` may hold only `PROCEDURAL`
code (checksum, cipher, joining, profile resolution) and the app-traced builder catalogue,
with one builder per message variant. The library is a client of the spec. The spec follows the
decompile, never the library's convenience.

Under an "additive-only" rule (never change a reader result while the library was mid-refactor),
`govee_ble.ksy` grew to 137 types, only 50 of them reachable from its frame root. The library
read 44 of the rest directly as standalone entry points, and most of the others hung off
those. Among them were parallel twins (`single_command_typed`, `status_reply_safe`,
`rx_frame`) and orphans (`mode_status`). The clean-spec refactor removed all of that.
Don't let it grow back.

1. **Fix the spec in place.** When the spec is wrong, correct the type that is wrong. Never add
   a side-by-side copy, a second entry point or a compatibility alias to dodge a change in a
   reader result. When a fix changes what a reader returns, the library change lands with it in
   one merge (`scripts/check.sh` green).
2. **Structure wins over use.** In the user's words: "I don't care if they are in use, if they
   are in the wrong place structurally then the client should fix their own code." A type in
   the wrong place is moved or removed, with no shim. "In use" only tells you which library code
   changes in the same merge. A type exists because the bytes have that shape, never because a
   library function wants that return value.
3. **One canonical path per message.** Each message (a frame body, a status TLV, an upload
   value, an advert) has one type path from one root. An `aa` reply and a 0xAC TLV are two
   messages that share one `govee_state` body. The path is chosen only by the wire bytes,
   `direction`, the device's `govee_profile` and, for an upload value, the caller's `kind` and
   `form`. No twins: no `typed`, `safe`, `sized` or `frame` variants, and no second framing.
   A form the app rejects but firmware emits is read and flagged with an `app_*` instance,
   never a strict/lenient pair. `valid` rejects only values neither the app nor the firmware
   produces. An app-side check over the read bytes (an upload gate) is a
   `VALIDATOR (read-side)` type. Library steps are `PROCEDURAL` doc notes.
4. **Six entry points, each labelled.** The only entry points are six file tops, and each
   one's doc starts `STANDALONE (<reason>)`:
   - `govee_ble_frame` (byte source: one GATT write or notification);
   - `govee_status_stream` (spans frames);
   - `govee_upload` (spans frames, or off-BLE);
   - `govee_manufacturer_data`, `govee_scan_record` and `govee_bgc_info` (other channels).

   The other three tops:
   - `govee_state` is the `SHARED PAYLOAD`: the same body inside an `aa` reply and a 0xAC TLV;
   - `govee_profile` is the `CONTEXT`: params only;
   - `govee_common` is the `LIBRARY`: enums and leaf types.

   No nested type is an entry point, and that covers the library too: it reads bytes only
   through the roots. Building a message may construct nested nodes. Reading a nested type from
   bytes on its own makes it an entry point; fix the client, not the spec. A new top of any
   kind needs the user's approval first.
5. **Context is params.** Direction, family overloads, counts, decode rules and masks are params
   or `govee_profile` selectors. `_root`, `_parent`, `io:`, `pos:`, `process`, `type: io`
   params and `ks-opaque-types` are banned. A new enum selector also goes in
   `devices.schema.json` under `client_profile.wire`; any other new profile fact goes in
   `client_profile`.
6. **Nothing dead.** Every type is reachable and every enum is applied; no enum has a single
   member. A change that orphans a type removes it in the same change. A field, param or
   instance kept only so a type or enum stays reachable (nothing reads it, or `if: false`)
   counts as dead.
7. **Cite, don't copy.** Each claim from the decompile cites it in `doc-ref`: the decompiled
   file's `path:line` (app or firmware), or firmware `IMAGE symbol @0xaddr` (lowercase hex).
   Prose, commits and code comments cite `Class.method[:line]`. A firmware-only item is labelled
   `FIRMWARE-SOURCED` ("Match the Govee app", rule 6); one shown only on hardware names the
   device and the observation in its doc. No decompiled code enters govee-ble-local: no pasted
   statements, casts or decompiler variable names, and no decompiled files or inventories.
   Describe the bytes in spec terms.

`spec/checks/run.sh` (Suite S, run by `scripts/check.sh` and CI) checks the structural part of
these. First ksc 0.11 compiles every `.ksy` read-write for Python and Java, then for JavaScript,
and fails on any output. Then `spec/checks/lint.py` runs:
- `meta`: file id = file name, MIT, ks-version 0.11, endianness; imports only other
  `spec/*.ksy` files;
- `roots`: rule 4's nine tops and their labels, no byte-reading type in the context or behind
  a param, and rule 3's twin names;
- `reach`: rule 6, orphans;
- `banned`: rule 5's banned constructs;
- `enums`: rule 6, enums;
- `schema`: rule 5, the enum selectors;
- `doc-ref`: rule 7, the citation format.

Suite S is a floor, not compliance. A change shaped to pass a lint while breaking a rule is a
violation: a renamed twin, a reference kept to hold a type alive, a type moved or relabelled to
dodge a check. Everything the lints can't see is for review:
- rules 1 and 2;
- why each path, selector and param exists;
- how the library reads bytes;
- that every claim carries a citation and the right label;
- `spec/*.py`;
- copied decompiled code.

Never weaken a lint or add an exemption to get past one.

If the decompile seems to need a parallel type, a new top or a lint change, stop and ask the
user.
