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
behaviour must match the app's for that SKU: frames, read sequences and value ranges.

1. **Send exactly what the app sends.** That means the same byte layout, frame count, order and
   values, even when hardware is known to accept something else. Example: the H60A6 works with a
   zero colour-temperature tint, but the app sends the tint, so the library sends it too (#19).
2. **Scope features to what the app does.** Don't send frames the app never sends, such as an
   all-bits mask where the app uses group frames, or values it can't produce, such as a kelvin
   off its 100 K tint table.
3. **When the library and the app disagree, the library is wrong.** Fix it to match, even if
   the current behaviour works on hardware. Verify on hardware where possible.
4. **Trace the path the app actually runs for that goodsType**: model, then detail screen,
   then view model, then builder. A lookalike class in another package doesn't count. Cite
   `Class.method` for every protocol claim (CONTRIBUTING: the decompiled app is the source of
   truth).
5. **If the app has more than one path** (for example the control screen vs automations or
   group control), match the control-screen path and document the other.

If matching the app looks impossible or harmful, stop and ask the user. Don't diverge on your
own judgement.
