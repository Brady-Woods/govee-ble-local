# Lockstep: frame-audit section E reader changes

This branch (`spec/frame-audit-E-lockstep`) sat on top of the additive section-E commit (02c198a). It held the
section-E forms that changed what an EXISTING generated reader returned. Each one had to land together with a
library change, so none of it was on the main spec branch.

Every form was traced in the Govee Home 7.7.02 decompile and checked by an adversarial reviewer. Each part's suite
results were measured on both library branches: fix/review-findings f182c20 and refactor/spec-client 29c28ed,
2053 tests each, all passing without this branch. The same 35 tests fail on both branches when this branch is
taken without the library edits below. With those edits, both branches pass again.

This file is kept as the record of that merge (c823fc5).

## 1. `single_command.params` cases for 33 B2 / 33 A3 / 33 14 (wire-build-61)

- What changes:
  - `command::secret_write` -> `secret_key_write`.
  - `command::gradual_wifi_ble` (A3) -> `gradual_write`.
  - `command::gradual` (14) -> `gradual_write`.
  - Each has the single layout the app sends:
    - SecretKeyController / BleUtils.p for B2;
    - ControllerGradual4BleWifi / the 0x14 gradual controller for A3 / 14.
  - `single_command_typed` (additive, already on the main branch) stays as an equivalent view.
  - There are no cases for 0x30, 0x36 or op 0x04. Their bodies are overloaded per family; use the standalone types.
- Reader effect:
  - harness: 1277 differences (B2 + A3: 853; 0x14: 424);
  - those frames now read typed;
  - a 33 B2 shorter than 8 secret bytes now raises EndOfStreamError (the app only sends 20-byte frames).
- Breakage: 32 tests on each branch. Root cause: `build._body()` puts bytes into the now-typed slot, so
  `SingleCommand._check` raises AttributeError ('bytes' object has no attribute '_root').
- Library edits for the same merge (frame bytes unchanged; both branches back to 2053 passed when applied in a
  scratch copy):
  1. `wire/build.py secret_check` (spec-client :799-800, fix :798-799). Keep the 8-byte check, and replace the
     body with
     `_write_frame(_F.Command.secret_write, lambda sc: _node(_F.SecretKeyWrite, sc, secret=bytes(secret)))`.
  2. `wire/build.py gradual` (spec-client :829-830, fix :828-829) becomes
     `_write_frame(_F.Command.gradual_wifi_ble, lambda sc: _node(_F.GradualWrite, sc, state=POWER_ON if on else POWER_OFF))`.
  3. `tests/test_wire_build.py:107`: replace `bytes(sec.params)[:8] == bytes(range(1, 9))` with
     `sec.params.secret == bytes(range(1, 9))`.
- The 0x14 case can be dropped on its own (land only B2 + A3); it adds no failures.

## 2. `enum: sub_mode` on `mode_read.selector_or_sub_mode` (wire-parse-11)

- What changes:
  - the field reads as `GoveeBleFrame.SubMode` for a member byte, and stays an int for a non-member byte
    (0x01 request selector, 0xa1);
  - the `rest` switch cases and the `scene_code` guard use enum literals;
  - the additive `sub_mode` instance drops its own `enum:` key (Java would not compile with it).
- Reader effect: only the field's type changes, 313 harness differences. With SubMode folded back to its int,
  there are 0 differences.
- Breakage: none on either branch (2053 passed). Optional. `int()`, f-string `:02x` and `==` with int still work
  on IntEnum. Code using `.name` must allow an int for non-member bytes.

## 3. `status_tlv` retypes (wire-reassemble-14 / -19 / -25 / -23, devices-fold-05)

- L1, 0x07 -> `status_device_info` (was `device_info_read`). This is the app's TLV 0x07 decode
  (Compose4BaseInfoSingleRead.w: only selectors 0x10 / 0x11 are decoded; others are left raw).
  - Breakage: 3 tests on each branch:
    - `tests/test_fix_wp2b_status_burst.py::test_walk_records_the_info_blocks_it_steps_past`
    - `tests/test_fix_wp2b_status_burst.py::test_analyzer_flags_a_stepped_past_info_block_and_walks_on`
    - `tests/test_spec_field_moves.py::test_a_0x07_block_the_app_skips_is_the_one_status_device_info_leaves_raw`
  - Library edits:
    - delete `_status_device_info`, `_app_skips` and the skip/seek branch (reassemble.py:221-240, :305-312);
    - in `parse_status` use `tlv.value`: skip when `not tlv.value.app_decodes`, else
      `parse.device_info_from(int(v.selector), v.info)`;
    - keep the fallback loop only to classify cuts;
    - update describe.py's docstring ("a 0x07 block the walk steps past");
    - update the 3 tests: the block is now in `tlvs`, with no skipped entry and no gap.
- L2, 0x05 -> `mode_read` over the exact TLV value (was `mode_status`).
  - Also changed: `cct_read_reply.kelvin_right` is read when `_io.size >= 5` (was `not _io.eof`), and
    `kelvin_right_signed` when `>= 5`. Without that, a 5-byte 0x15 TLV, which the app reads, would fail.
  - Breakage: none.
  - Library edits: none forced. To adopt:
    - `reassemble.py:404` becomes `st.mode = tlv.value`;
    - `_fold.py:180` becomes `apply_mode(profile, state, st.mode)`;
    - drop the `ljust` pad in `parse.mode_value` (parse.py:221-227);
    - `tests/test_mode_read_decoders.py:83` then needs a ModeRead;
    - a short 0x05 value then gives no state, as the app's Q throws.
- L3, 0xA5 `color_group_status.records`: repeat-eos -> repeat-expr `(_io.size - 1) / 4`, plus a `trailing` field.
  A length that is not 1 + 4k no longer raises.
  - Breakage: none.
  - Library edits: none. Count records with the additive `color_group_status_counted(piece_count, 4, 1)`.

## Measured but NOT included: `read_command` 0xA5 retype (wire-parse-21)

Retyping `read_command`'s `local_color_read` case from `bulb_group_color_read_v2` to `color_group_status` breaks
5 tests per branch:

- `test_mechanism_b_segments_not_overwritten_by_stale_poll`
- `test_mechanism_b_assembles_positional_segments`
- `test_mechanism_b_drops_batches_outside_the_walk`
- `test_walk_mechanism_b_asks_each_batch_in_order`
- `test_mechanism_b_v2_batch_and_assembly`

The frame carries no discriminator, so the H61A8 V2 batch reply and the direct `aa a5 <group>` reply cannot both
be typed by one case. The additive `color_group_direct_frame(record_count)` (main branch) covers wire-parse-21
without it.

## Applying all of it

Merge this branch together with the library edits in sections 1 and 3 (L1). Sections 2, 3 (L2) and 3 (L3) need
no library edit.
