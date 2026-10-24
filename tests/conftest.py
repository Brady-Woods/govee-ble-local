"""Make ``govee_ble_local`` (src layout) — including the generated Kaitai classes
under ``govee_ble_local._generated`` — and the spec builders (``tests/spec_frames``)
importable whether or not the package is installed editable."""
from __future__ import annotations

import pathlib
import sys

_HERE = pathlib.Path(__file__).resolve().parent
_SRC = _HERE.parent / "src"

for _p in (str(_SRC), str(_HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_GENERATED = _SRC / "govee_ble_local" / "_generated"


def kaitai_outputs(root: pathlib.Path) -> tuple[str, ...]:
    """The Kaitai classes of ``root``'s spec: one module per ``spec/*.ksy``."""
    return tuple(f"{p.stem}.py" for p in sorted((root / "spec").glob("*.ksy")))


def generated_outputs(root: pathlib.Path) -> tuple[str, ...]:
    """The generated modules of ``root``'s spec: the Kaitai classes, and the device table for
    ``spec/devices.yaml`` (not ``__init__.py``: written once)."""
    table = ("devices.py",) if (root / "spec" / "devices.yaml").is_file() else ()
    return (*table, *kaitai_outputs(root))


def generated_classes_problem(root: pathlib.Path) -> str | None:
    """Why ``root``'s generated Kaitai classes can't be tested against, or None when they can.

    They are a build product (gitignored), so they can be missing, and an ignored ``_generated/``
    survives a ``git switch`` or ``git pull`` that changes the spec: then they are older than
    ``spec/*.ksy``, ``spec/devices.yaml`` (or its schema, which the table's projection follows), the
    catalogue snapshot (``spec/govee_catalog.json``, whose routes the table carries), the scene store
    (``scenes/store.json``: only a SKU with a catalog is offered scenes), a generator, or the
    device resolver (``devices/_resolve.py``, whose admission rule decides the table's cut). Git gives
    every file a checkout rewrites a fresh mtime, so an input
    newer than the oldest output means the classes were built from something else (a touch or a stash
    round trip only costs a needless regeneration)."""
    generated = root / "src" / "govee_ble_local" / "_generated"
    outputs = generated_outputs(root)
    missing = [n for n in outputs if not (generated / n).is_file()]
    if missing:
        return (f"src/govee_ble_local/_generated/ is missing {', '.join(missing)}: run "
                "`bash tools/gen_kaitai.sh` (needs the JVM kaitai-struct-compiler 0.11) or "
                "`pip install -e .`, which generates them")
    inputs = [*(root / "spec").glob("*.ksy"), root / "tools" / "gen_kaitai.sh",
              root / "spec" / "devices.yaml", root / "spec" / "devices.schema.json",
              root / "spec" / "govee_catalog.json", root / "tools" / "gen_devices.py",
              root / "src" / "govee_ble_local" / "devices" / "_resolve.py",
              root / "src" / "govee_ble_local" / "scenes" / "store.json"]
    oldest_output = min((generated / n).stat().st_mtime for n in outputs)
    stale = sorted(p.name for p in inputs if p.is_file() and p.stat().st_mtime > oldest_output)
    if stale:
        return (f"src/govee_ble_local/_generated/ is older than {', '.join(stale)} (edited, or changed by a "
                "checkout or pull since it was generated): run `bash tools/gen_kaitai.sh`")
    return None


def pytest_sessionstart(session):  # type: ignore[no-untyped-def]
    """Stop with a clear message, not an import error in every test or results from another branch's
    classes, when the generated Kaitai classes are missing or stale."""
    problem = generated_classes_problem(_HERE.parent)
    if problem:
        import pytest

        pytest.exit(problem, returncode=4)


# -- optional-dependency gate ---------------------------------------------------------------
import importlib.util  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
from types import SimpleNamespace  # noqa: E402
from typing import Any  # noqa: E402

import pytest  # noqa: E402


def optional_deps_required() -> bool:
    """True on CI (``CI`` truthy: 1/true/yes) or with ``GOVEE_REQUIRE_OPTIONAL_TESTS=1``: a missing optional
    test dependency must FAIL rather than silently skip (conformance protection must not vanish)."""
    flag = os.environ.get("GOVEE_REQUIRE_OPTIONAL_TESTS", "")
    truthy = ("1", "true", "yes")
    return os.environ.get("CI", "").lower() in truthy or flag.lower() in truthy


def require_optional(modname: str, *, reason: str | None = None):  # type: ignore[no-untyped-def]
    """``pytest.importorskip`` that fails instead of skipping when :func:`optional_deps_required`."""
    try:
        return pytest.importorskip(modname, reason=reason)
    except pytest.skip.Exception:
        if optional_deps_required():
            pytest.fail(
                f"required optional test dependency {modname!r} is missing"
                + (f" ({reason})" if reason else ""),
                pytrace=False,
            )
        raise


def read_struct(obj):  # type: ignore[no-untyped-def]
    """Read a generated Kaitai object and return it. The generated classes are read-write
    (``ReadWriteKaitaiStruct``): ``from_bytes`` / ``Cls(..., stream)`` only bind the stream."""
    obj._read()
    return obj


def scene_store(sku: str, catalog: dict[str, dict[str, Any]]) -> str:
    """The text of a scene store (``scenes/store.json``) holding ``catalog`` ({name: entry}) for ``sku``."""
    pool = [{"name": name, **entry} for name, entry in sorted(catalog.items())]
    return json.dumps({"pool": pool, "skus": {sku: list(range(len(pool)))}, "by_set": {}})


def routing_pact(sku: str, *, goods_type: int | None = None, allow_modelled: bool = False,
                 profile: Any = None) -> tuple[int, int] | None:
    """None where ``sku`` routes without a pact (at ``goods_type`` where given), else the lowest pact one
    of its rows (at ``goods_type``) lists: a pact-conditioned reassignment's target row is served on an
    advertised pact alone (profile_for), so a SKU it routes needs one there, also when its source row
    routes it without. With ``profile``, the same for the SKU's routes to that profile (a pact route's, such
    as H60A6's at (1, 1), is taken at its pact alone)."""
    from govee_ble_local.devices import profile as profile_mod

    found = profile_mod.profile_for(sku, goods_type=goods_type, allow_modelled=allow_modelled)
    if found is not None and (profile is None or found is profile):
        return None
    tables = (profile_mod._ADMITTED, profile_mod._MODELLED) if allow_modelled else (profile_mod._ADMITTED,)
    pacts = sorted(pact for table in tables for route in table[1].get(sku.upper(), ())
                   if (goods_type is None or route.goods_type == goods_type)
                   and (profile is None or route.profile is profile) for pact in route.pacts or ())
    return pacts[0] if pacts else None


def no_heartbeat(profile: Any) -> Any:
    """``profile`` sending no heartbeat (1.5.0's behaviour): for a test that pins exactly what a link held
    2 s or more sends, or the device's own timer without the beat. The documented opt-out, the one field
    (its timing, ``after_quiet`` on goodsType 43, is then ignored)."""
    import dataclasses

    return dataclasses.replace(profile, heartbeat=None)


# A valid RGB scene blob (ScenesRgb.isValidProtocolBytes, three V1 a3 frames): H61A8's Halloween effect at
# code 110, which its library response lists after the cmdVersion-1 one (code 2497) the catalog holds.
RGB_BLOB = "gwb/9QAFAP///wUA/+n/BQD///8FAP/p2QUA//j/BgAE/x4A/1oA/zIA/3gA"


def gated_scene_profile() -> Any:
    """H6168's profile with the scene facts its devices.yaml row gives (goodsType 136: {0, 1, 2} below part
    brightness, {0, 1, 2, 3} from soft 1.06.00 and hard 1.00.02), which the generated table leaves out
    until a catalog is bundled for it (the data commit): a device whose scene set follows its firmware."""
    import dataclasses

    from govee_ble_local._generated import devices
    from govee_ble_local.devices import _resolve
    from govee_ble_local.devices.profile import profile_for
    from govee_ble_local.models import Capability

    fields = next(f for skus, f, _routes in _resolve.profiles(devices.FAMILIES, devices.MODE_COLOR_PARSE,
                                                              _resolve.LEVELS) if "H6168" in skus)
    base = profile_for("H6168")
    assert base is not None and fields["scene_dialect"] == base.scene_dialect
    return dataclasses.replace(base, capabilities=base.capabilities | {Capability.SCENES},
                               scene_versions=frozenset(fields["scene_versions"]),
                               scene_versions_by_firmware=fields["scene_versions_by_firmware"])


def gated_scene_store() -> str:
    """A scene store for H6168 (gated_scene_profile) of three of H61A8's bundled scenes, each as the app lists
    it at {0, 1, 2}: "Awaken" (op type 3 only) is hidden there, "Halloween" (op type 3, then the RGB
    RGB_BLOB at code 110) plays code 110 there, and "Plain" (an op-1 static code) is listed as it is."""
    from govee_ble_local.scenes import load_scenes

    def entry(name: str, scene: Any) -> dict[str, Any]:
        own = {"code": scene.code, "param": scene.param, "category": scene.category,
               "param_id": scene.param_id, "scene_type": scene.scene_type, "cmd_version": scene.cmd_version}
        return {"name": name, **{k: v for k, v in own.items() if v is not None}}

    bundled = load_scenes("H61A8")
    awaken, halloween = entry("Awaken", bundled["Awaken"]), entry("Halloween", bundled["Halloween"])
    assert (awaken["scene_type"], awaken["cmd_version"], halloween["code"]) == (2, 1, 2497)
    there = {"name": "Halloween", "code": 110, "param": RGB_BLOB, "category": "Festival", "param_id": 133,
             "scene_type": 1, "cmd_version": 0}
    plain = {"name": "Plain", "code": 7, "param": None, "category": "c", "scene_type": 1}
    pool = sorted([awaken, halloween, there, plain],
                  key=lambda e: (e["name"], json.dumps(e, sort_keys=True, separators=(",", ":"))))
    at = {id(e): i for i, e in enumerate(pool)}
    return json.dumps({"pool": pool, "skus": {"H6168": sorted(at[id(e)] for e in (awaken, halloween, plain))},
                       "by_set": {"H6168": {"0,1,2": {"Awaken": None, "Halloween": at[id(there)]}}}})


# A SKU no device table row lists, whose scene sets tools/fetch_scenes.py is given (with_scene_sets): the
# empty set, {0, 1, 2} (a pact's) and {0, 1, 2, 3} (a firmware gate's, the union), so the tool's per-set
# evaluation runs at a null op set and at a smaller one, as on a dreamcolorlightv1 SKU.
SETS_SKU = "HSETS"


def with_scene_sets(fs: Any, monkeypatch: pytest.MonkeyPatch, sku: str = SETS_SKU) -> None:
    """Give the tool ``fs`` (tools/fetch_scenes.py, loaded) a legacy_scene row for ``sku`` whose scene sets
    are the empty one, ``by_pact`` {0, 1, 2} and ``by_firmware`` {0, 1, 2, 3}, beside the table's rows."""
    real = fs.scene_rows
    row = {"capabilities": ("scenes",), "scene_versions": (), "scene_dialect": "A",
           "scene_versions_by_pact": (((1, 2), (0, 1, 2)),),
           "scene_versions_by_firmware": (("1.06.00", "1.00.02", (0, 1, 2, 3)),)}
    monkeypatch.setattr(fs, "scene_rows", lambda levels=None: {**real(levels), sku: [row]})


class FakeSceneFiles:
    """Stands in for ``scenes.resources.files(...)``: reading ``store.json`` returns ``store`` (a store's
    text, :func:`scene_store`), or raises it when it is an exception, or a missing file's error when None."""

    def __init__(self, store: str | Exception | None) -> None:
        self._store = store

    def __truediv__(self, file: str) -> Any:
        def read_text(encoding: str | None = None) -> str:
            assert encoding == "utf-8"
            if file != "store.json" or self._store is None:
                raise FileNotFoundError(2, "No such file", file)
            if isinstance(self._store, Exception):
                raise self._store
            return self._store
        return SimpleNamespace(read_text=read_text)


# -- tools/field_test, offline ---------------------------------------------------------------------
@pytest.fixture(autouse=True)
def field_test_offline(monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory) -> None:
    """tools/field_test never scans for or connects to a real device in a test (both raise), its device refs
    are keyed by a key of the test session's and its secrets file is the session's (absent), never the
    tester's own (~/.cache, ~/.config/govee-ble-local), and its clock reads midday, inside the write window,
    so a test passes at any hour (CI runs in UTC); a window test sets guard.now itself."""
    base = tmp_path_factory.getbasetemp()
    monkeypatch.setenv("GOVEE_FIELD_TEST_NO_SCAN", "1")
    monkeypatch.setenv("GOVEE_FIELD_TEST_NOW", "2026-10-23T12:00:00")
    monkeypatch.setenv("GOVEE_FIELD_TEST_REF_KEY", str(base / "field-test-ref.key"))
    monkeypatch.setenv("GOVEE_FIELD_TEST_SECRETS", str(base / "field-test-secrets.json"))


# -- firmware-image provenance ----------------------------------------------------------------
_PROVENANCE_RAN: set[str] = set()


@pytest.fixture(autouse=True)
def firmware_provenance(request: pytest.FixtureRequest):  # type: ignore[no-untyped-def]
    """Fail a test in which a Device whose SKU has a spec/firmware.yaml image reads a frame or advert that
    image didn't send it (fake_ble.Provenance), or sends a request an image can't read. Such a device is
    answered by its image (fake_ble.FirmwareGovee), never by a hand-built frame."""
    _PROVENANCE_RAN.add(request.node.nodeid)
    if importlib.util.find_spec("yaml") is None:   # PyYAML is a test dependency; the images need it
        yield None
        return
    from fake_ble import Provenance

    provenance = Provenance()
    provenance.install()
    try:
        yield provenance
    finally:
        provenance.uninstall()
    found = provenance.verify()
    if found:
        pytest.fail("built by hand where the firmware image answers:\n" + "\n".join(found), pytrace=False)


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_call(item: pytest.Item) -> None:
    """The provenance check can't be switched off: a module or class that overrides its fixture fails."""
    if item.nodeid not in _PROVENANCE_RAN:
        pytest.fail("the firmware_provenance fixture is overridden here; it must run for every test",
                    pytrace=False)
