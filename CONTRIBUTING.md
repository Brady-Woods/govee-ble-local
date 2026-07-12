# Contributing

`govee-ble-local` is the local-BLE protocol engine behind a Home Assistant integration. It aims for
the HA **Platinum** quality tier, so contributions should keep it fully async, strictly typed, and
lean on dependencies.

## Ground rules

- **Source of truth is the decompiled Govee Home app (Java).** `spec/*.ksy` + `spec/devices.yaml` are
  derived from it; when they disagree with the Java, the Java wins. Cite `Class.method:line` for
  protocol claims. Real hardware confirms behaviour; a single packet capture only illustrates it.
- **Dependencies:** runtime deps are limited to `bleak`, `bleak-retry-connector`, `cryptography`, and
  `kaitaistruct` (see `pyproject.toml`). The first three ship with Home Assistant; `kaitaistruct` is
  the one accepted exception (pure-Python, stdlib-only). **Don't add a runtime dependency without
  justification, favour the standard library, and never require a version newer than HA ships.**
- **Typing:** the package ships `py.typed`; keep `mypy --strict` clean.

## Development

```bash
python -m pip install -e '.[test,typing]'    # dev install
python -m pytest -q                          # offline test suite
python -m mypy --strict src/govee_ble_local  # strict type check
bash tools/gen_kaitai.sh                      # regenerate readers after editing spec/*.ksy
bash tools/gen_docs.sh                        # build the pdoc API reference (needs .[docs])
```

`pytest` and `mypy --strict` **must be green before you push.** If you change `spec/*.ksy`, regenerate
the `_generated/` readers and commit them.

## Branching & commits

- **Branch for anything non-trivial:** `feat/<slug>`, `fix/<slug>`, `docs/<slug>`, `refactor/<slug>`,
  `chore/<slug>`. Open a PR; don't push substantial changes straight to `master`. (Small, low-risk
  doc/typo fixes may go directly to `master`.)
- **Commit messages** follow a Conventional-Commits-style subject (`type: summary`, imperative, ≤72
  chars) plus a body that explains the **why** and records any **source citation** (`Class.method:line`)
  or **hardware verification** (which device, what was observed). End co-authored work with:
  ```
  Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
  ```
- **Update `CHANGELOG.md` in the same commit** — add entries under `[Unreleased]`, grouped
  Added / Changed / Fixed / Spec (Spec = the Kaitai `.ksy` + `devices.yaml` model).

## Releases (Semantic Versioning — https://semver.org/)

`1.0.0` is the initial public baseline; not published to PyPI yet. The public API contract is
`govee_ble_local.__all__`.

**A version bump and a release are one action, not two.** `origin/master`'s `pyproject.toml`
version must always have a matching tag and a published (non-prerelease) GitHub Release with
built artifacts attached — a bumped-but-untagged version on `master` is an incomplete release,
not a "cut the tag later" backlog item. To cut a release, in one sitting:
1. Bump `version` in `pyproject.toml` (MAJOR = breaking public API, MINOR = additive, PATCH = fixes)
   and move `[Unreleased]` → `[X.Y.Z] - DATE` in `CHANGELOG.md`.
2. `python -m build`; smoke-verify the sdist/wheel (installs, imports, correct version + runtime deps).
3. `git tag -a vX.Y.Z -m "..."`, `git push origin vX.Y.Z`.
4. `gh release create vX.Y.Z --notes-file <the CHANGELOG section> dist/*.whl dist/*.tar.gz`.

Use `--prerelease` tags (e.g. `vX.Y.Zrc1`) only to hand a client a testable build of an unmerged
branch/PR — once that work merges to `master` under its real version number, it still needs the
real, non-prerelease tag + release; the prerelease doesn't substitute for it.

## Diagnostics

When debugging device behaviour, enable the frame-tier capture and analyse it — see
[`docs/DIAGNOSTICS.md`](docs/DIAGNOSTICS.md) and the `govee-ble-analyze` CLI. Never commit raw captures
(`*.jsonl` / `btsnoop*.log` are gitignored) — they contain device identifiers and the account-lock secret.
