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
  Don't put upper bounds on HA-shipped dependencies either: HA installs requirements under its own
  `package_constraints.txt`, so a ceiling only causes conflicts when HA moves (issue #8).
- **Typing:** the package ships `py.typed`; keep `mypy --strict` clean.

## Dev setup & quality gates

```bash
python3 -m venv .venv && source .venv/bin/activate
python -m pip install -e '.[dev]'   # pytest stack, cloud, mypy, ruff, bandit, pip-audit,
                                    # vulture, radon, pycryptodome (test-only AES reference), uv;
                                    # also generates the Kaitai classes (needs the JVM ksc, below)
python -m pytest -q                 # offline test suite
bash tools/gen_kaitai.sh            # regenerate the read-write classes after editing spec/*.ksy,
                                    # or after a checkout or pull that changes it (pytest stops
                                    # and says so when the classes are older than the spec)
bash tools/gen_docs.sh              # build the pdoc API reference (needs .[docs])
```

The Kaitai read-write classes in `src/govee_ble_local/_generated/` are a **build product** of
`spec/*.ksy`, gitignored and never committed. Every build generates them: the in-tree PEP 517 backend
(`_build_backend/`, `[build-system] backend-path`) runs `tools/gen_kaitai.sh` before setuptools builds an
sdist, a wheel or an editable install, so `pip install -e .`, `pip install .` and `python -m build` need
the JVM `kaitai-struct-compiler` 0.11 (`brew install kaitai-struct-compiler`; on Debian/Ubuntu
`bash scripts/install_ksc.sh`). The sdist and wheel ship the generated classes, so installing a
released wheel or sdist needs no compiler. A git or VCS URL (`pip install "govee-ble-local @
git+https://...@ref"`) or a GitHub "Source code" archive is a repository build too, and needs bash and the
JVM compiler; Home Assistant has neither. To test an unreleased build in Home Assistant, pin the wheel
attached to a prerelease GitHub Release (`govee-ble-local @
https://github.com/Brady-Woods/govee-ble-local/releases/download/vX.Y.ZrcN/govee_ble_local-X.Y.ZrcN-py3-none-any.whl`),
never a git ref. The integration's `pip install --dry-run` dependency check doesn't catch a git ref,
because preparing metadata skips the generation step. Without the classes, or with classes older than
`spec/*.ksy` or `tools/gen_kaitai.sh`, the test suite stops at once and says to run the generator.

**`bash scripts/check.sh` must pass before you push** (it is the code gate CI and the release
workflow run, and they also run the artifact smoke test below; it builds its own venv, `.venv-check`,
override with `VENV=`). It needs the JVM `kaitai-struct-compiler` 0.11 (`brew install
kaitai-struct-compiler`; on Debian/Ubuntu, `bash scripts/install_ksc.sh`, which is what CI uses). It
enforces, fail-fast:

1. `tools/gen_kaitai.sh` generates the read-write classes in `_generated/` from `spec/*.ksy`.
2. `ruff check src tools tests spec _build_backend` (rules in `[tool.ruff.lint]`)
3. `mypy src/govee_ble_local _build_backend` (`--strict`; generated classes exempt)
4. `bandit -r src tools _build_backend` failing on Medium+ severity
5. `pip-audit` over the package's resolved runtime dependencies (no ignores); skipped offline unless
   `CI=true`
6. `pytest` with a coverage floor of 85% (`COV_MIN`) over the package (generated readers omitted)

With `CI=true`, missing optional test dependencies fail the run instead of skipping tests.

CI and the release workflow also run `bash scripts/smoke_artifacts.sh` (release: `--no-build`, after its
Build step): it checks that the sdist ships the generated classes and no `spec/` or `tools/`, then installs
the wheel, and a wheel built from the sdist, into fresh venvs and imports the package, so a wheel without
the generated classes can't be published. To run it locally, point it at Python 3.11 or later
(`PYTHON=python3.13 bash scripts/smoke_artifacts.sh`); it builds in a throwaway venv and needs network.

`bash scripts/audit.sh` is an informational, never-failing report (vulture dead code, radon
complexity/maintainability, ruff statistics). It is not part of the gate.

If you change `spec/*.ksy`, re-run `bash tools/gen_kaitai.sh` (or `scripts/check.sh`) before testing. An
editable install generates the classes only once, at install time, and they are a build product that is
never committed. The library both reads and writes every frame with them, so `wire/build.py` changes only
when the app sends a different frame, not when a layout moves.

### Hardware tools

`tools/` holds live-device probes and capture analysers. They are developer utilities, not part of
the package or the test suite, and need a Linux/BlueZ host and a real device. Run them from the
repo root, e.g. `python3 tools/h60a6_live_check.py --address AA:BB:CC:DD:EE:FF`; see
[`tools/README.md`](tools/README.md) for each tool, its arguments and the BLE host gotchas. From a
checkout, run `bash tools/gen_kaitai.sh` (or `pip install -e .`) first: the tools put `../src` first on
`sys.path`, and its `_generated/` classes are generated, not committed.

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
- The pinned JVM `kaitai-struct-compiler` 0.11 is installed (`scripts/install_ksc.sh`). The
  `check.sh` gate generates the classes, and the Build step generates them again: the in-tree backend
  reruns `tools/gen_kaitai.sh` for the sdist, and the wheel is built from that sdist.
- The full `scripts/check.sh` gate passes (with `CI=true`, so pip-audit and optional test
  dependencies are mandatory).
- The tag equals `v` + the `pyproject.toml` version.
- Only then does it build the sdist and wheel (`python -m build`).
- `scripts/smoke_artifacts.sh --no-build`: the sdist must ship the generated classes and no `spec/` or
  `tools/`, and the wheel, and a wheel pip builds from the sdist, are installed into fresh venvs and must
  import and round-trip a frame. Only then is `dist/` uploaded as an artifact; the separate `publish` job
  (environment `pypi`, `id-token: write`) uploads it to PyPI.

There is no TestPyPI dry run wired into the workflow. To check packaging locally without
publishing, run `PYTHON=python3.13 bash scripts/smoke_artifacts.sh` (builds into `dist/`; needs the JVM
compiler and network) and `twine check dist/*`.


## Diagnostics

When debugging device behaviour, enable the frame-tier capture and analyse it — see
[`docs/DIAGNOSTICS.md`](docs/DIAGNOSTICS.md) and the `govee-ble-analyze` CLI. Never commit raw captures
(`*.jsonl` / `btsnoop*.log` are gitignored) — they contain device identifiers and the account-lock secret.
