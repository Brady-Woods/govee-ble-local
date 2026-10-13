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

## Dev setup & quality gates

```bash
python3 -m venv .venv && source .venv/bin/activate
python -m pip install -e '.[dev]'   # pytest stack, cloud, mypy, ruff, bandit, pip-audit,
                                    # vulture, radon, pycryptodome (test-only AES reference), uv
python -m pytest -q                 # offline test suite
bash tools/gen_kaitai.sh            # regenerate readers after editing spec/*.ksy
bash tools/gen_docs.sh              # build the pdoc API reference (needs .[docs])
```

**`bash scripts/check.sh` must pass before you push** (it is exactly what CI and the release
workflow run; it builds its own venv, `.venv-check`, override with `VENV=`). It enforces, fail-fast:

1. `ruff check src tools tests spec` (rules in `[tool.ruff.lint]`)
2. `mypy src/govee_ble_local` (`--strict`; generated readers exempt)
3. `bandit -r src tools` failing on Medium+ severity
4. `pip-audit` with three justified ignores (PYSEC-2026-3552/3553/3554: `cryptography` PKCS#7/X.509
   issues this library does not use); skipped offline unless `CI=true`
5. `pytest` with a coverage floor of 85% (`COV_MIN`) over the package (generated readers omitted)

With `CI=true`, missing optional test dependencies fail the run instead of skipping tests.

`bash scripts/audit.sh` is an informational, never-failing report (vulture dead code, radon
complexity/maintainability, ruff statistics). It is not part of the gate.

If you change `spec/*.ksy`, regenerate the `_generated/` readers and commit them.

### Hardware tools

`tools/` holds live-device probes and capture analysers. They are developer utilities, not part of
the package or the test suite, and need a Linux/BlueZ host and a real device. Run them from the
repo root, e.g. `python3 tools/h60a6_live_check.py --address AA:BB:CC:DD:EE:FF`; see
[`tools/README.md`](tools/README.md) for each tool, its arguments and the BLE host gotchas.

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

## Releasing

Releases are published to PyPI by `.github/workflows/release.yml` when a `v*` tag is pushed, using
PyPI trusted publishing (no API token is stored in the repo). The public API contract is
`govee_ble_local.__all__`; versions follow [Semantic Versioning](https://semver.org/) (MAJOR =
breaking public API, MINOR = additive, PATCH = fixes).

### One-time setup (repository owner)

1. Create or log in to a PyPI account with 2FA enabled.
2. At <https://pypi.org/manage/account/publishing/> add a **pending trusted publisher**:
   project name `govee-ble-local`, owner `Brady-Woods`, repository `govee-ble-local`, workflow
   `release.yml`, environment `pypi`.
3. In GitHub, Settings -> Environments, create an environment named `pypi`. Add yourself as a
   required reviewer so a tag push cannot publish unattended.

### Per release

**A version bump and a release are one action, not two.** `origin/master`'s `pyproject.toml` version must
always have a matching tag **and** a published (non-prerelease) GitHub Release with the built artifacts
attached; a bumped-but-untagged version on `master` is an incomplete release. Prerelease tags (`vX.Y.ZrcN`)
are only for handing someone a testable build of an unmerged branch.

1. Bump `version` in `pyproject.toml`; rename `## [Unreleased]` in `CHANGELOG.md` to
   `## [X.Y.Z] - YYYY-MM-DD` (and add a fresh empty `[Unreleased]`).
2. Merge to `master` (the tagged commit must be on `master`).
3. `git tag vX.Y.Z && git push origin vX.Y.Z`
4. Watch the **Release** workflow; if the `pypi` environment has required reviewers, approve it.
5. Create the GitHub Release from the same artifacts the workflow built (so they match what went to
   PyPI): `gh run download <run-id> -n dist -D dist/` then
   `gh release create vX.Y.Z --notes-file <the CHANGELOG section> dist/*.whl dist/*.tar.gz`
   (non-prerelease).
6. Verify in a clean venv: `pip install govee-ble-local==X.Y.Z` and
   `python -c "import govee_ble_local as g; print(g.__version__)"`.

### What the workflow enforces

- The tagged commit is an ancestor of `origin/master`.
- The full `scripts/check.sh` gate passes (with `CI=true`, so pip-audit and optional test
  dependencies are mandatory).
- The tag equals `v` + the `pyproject.toml` version.
- Only then does it build the sdist and wheel (`python -m build`) and upload them as an artifact;
  the separate `publish` job (environment `pypi`, `id-token: write`) uploads them to PyPI.

There is no TestPyPI dry run wired into the workflow. To check packaging locally without
publishing, build from a clean checkout and run `twine check dist/*`.

Note: `README.md` currently says the package is "Not on PyPI yet". Update the Install section after
the first successful publish.

## Diagnostics

When debugging device behaviour, enable the frame-tier capture and analyse it — see
[`docs/DIAGNOSTICS.md`](docs/DIAGNOSTICS.md) and the `govee-ble-analyze` CLI. Never commit raw captures
(`*.jsonl` / `btsnoop*.log` are gitignored) — they contain device identifiers and the account-lock secret.
