"""tools/fetch_scenes.py keeps a stored scene name on its scene: a seeded, bounded property test.

Random multi-round scene-library histories (and a few fixed ones) are replayed offline through main() over a
temporary store and tombstone. Every scene has a hidden identity the tool never reads: its effects' param ids,
codes and blobs are never reused. Checked after every fetch:

  INV       a stored name keeps its scene, and a change of its code or category is printed; a printed keep
            never moves it to another scene, and only a printed guess does, off a scene the library still
            answered at the SKU's previous fetch and no longer lists (N2, N3)
  UNIQ      every listed scene is stored, each under a name of its own (but one main() cuts for sceneType
            5: the blob --resolve baked in is a new-DIY one)
  IDEM      an identical re-fetch changes nothing and prints no change
  SAME      a first fetch names a response alike whatever the store holds (N6: no other SKU's names)
  DET       shuffling the categories and scenes of every response changes no stored catalog
  RETIRE    a removed name comes back only on its own scene, exactly, and printed; its scene's exact return
            (a resolved blob's: as its placeholder; a placeholder's: with its param id) takes it back; it
            stays retired, a reclaim too (N1)
  KEEP      a name is removed only for a scene refused for good or cut (sceneType 5), and printed (N3, N4)
  FOLLOW    a name whose scene the app now plays at another of its own lightEffects follows it (N3)
  RESOLVED  a blob --resolve baked in stays while the library ships that scene as a placeholder (N7)
  STAY      a listed scene keeps the name of the one stored scene of its sceneName with its code, category,
            blob and param id, where it is the one listed so (N5)
"""
from __future__ import annotations

import base64
import contextlib
import copy
import functools
import importlib.util
import io
import json
import random
import re
import urllib.parse
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

TOOLS = Path(__file__).resolve().parent.parent / "tools"
SKUS = ("H6641", "H6047", "H6006")   # op sets {0, 1, 2, 3}, {0, 1, 2, 3} and {0, 1}; all legacy_scene
NAMES = ("Flash", "Glow", "Dusk", "Rain")
CATS = ("Emotion", "Party", "Life", "Zootopia 2", "Natural")
EDITS = ("reorder", "add_cat", "rm_cat", "rename_cat", "renumber", "blob", "pid", "dup_same", "dup_other",
         "literal", "exp_cat", "expired", "rules", "refused", "type5", "rm_scene", "placeholder",
         "rename_scene", "restore")
GONE = {"checkEffectiveTime": 1, "effectiveTimes": [{"startTime": 0, "endTime": 1}]}
P50 = base64.b64encode(b"\x50\x01\x02").decode()   # a new-DIY blob, which a legacy_scene SKU cuts
NAME = re.compile(r'"((?:[^"]|"(?! \(code))*)" \(code')
LINES = {"keep": "stored names keep their scene", "guess": "stored names are guessed onto",
         "removed": "removed ", "back": "retired names come back", "apart": "share a name with another",
         "unsure": "are ambiguous"}
Lib = list[dict[str, Any]]


def _resolved(pid: int, scene_type: object = None) -> str:
    """The blob the offline --resolve bakes in for a param id: a new-DIY one (0x50) for sceneType 5."""
    return base64.b64encode((b"\x50" if scene_type == 5 else b"\x93") + pid.to_bytes(3, "big")).decode()


def _same(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """STAY's key: one code, category, blob and param id."""
    return all(a.get(k) == b.get(k) for k in ("code", "category", "param", "param_id"))


class Gen:
    """Scenes for one history: every uid, code, param id and blob is new."""

    def __init__(self, seed: int) -> None:
        self.rng, self.n = random.Random(seed), 0  # noqa: S311 - reproducible histories, not a key

    def next(self) -> int:
        self.n += 1
        return self.n

    def blob(self) -> str:
        return base64.b64encode(b"\x93\x01" + self.next().to_bytes(2, "big")).decode()

    def effect(self, **kw: Any) -> dict[str, Any]:
        e: dict[str, Any] = {"code": 100 + self.next(), "pid": self.next(), "param": self.blob(), "stype": 1,
                             "cmdv": 0}
        x = self.rng.random()
        if x < 0.15:
            e["param"] = None                        # bare: activated by code
        elif x < 0.28:
            e.update(param=None, big=True)           # a placeholder
        elif x < 0.38:
            e.update(stype=2, cmdv=self.rng.choice((0, 1)))   # RGBIC: H6006's op set refuses it
        return {**e, **kw}

    def scene(self, name: str | None = None) -> dict[str, Any]:
        effects = [self.effect()]
        if self.rng.random() < 0.15:   # which one plays depends on the SKU's op set
            effects.insert(0, self.effect(stype=2, cmdv=self.rng.choice((0, 1))))
        return {"uid": self.next(), "name": name or self.rng.choice(NAMES), "effects": effects}

    def library(self) -> Lib:
        lib: Lib = []
        for cat in self.rng.sample(CATS, self.rng.randrange(1, 4)):
            lib.append({"name": cat, "scenes": [self.scene() for _ in range(self.rng.randrange(1, 5))]})
        return lib

    def edit(self, lib: Lib) -> None:
        r = self.rng
        scenes = [(c, s) for c in lib for s in c["scenes"]]
        kind = r.choice(EDITS) if scenes else "add_cat"
        if kind == "reorder":
            r.shuffle(lib)
            for c in lib:
                r.shuffle(c["scenes"])
        elif kind == "add_cat":
            free = [n for n in CATS if n not in {c["name"] for c in lib}] or ["Extra"]
            lib.append({"name": r.choice(free), "scenes": [self.scene() for _ in range(r.randrange(1, 4))]})
        elif kind == "rm_cat":
            lib.remove(r.choice(lib))
        elif kind == "rename_cat":
            free = [n for n in (*CATS, "Zootopia", "Life 2") if n not in {c["name"] for c in lib}]
            r.choice(lib)["name"] = r.choice(free)
        else:
            c, s = r.choice(scenes)
            e = r.choice(s["effects"])
            if kind == "renumber":
                e["code"] = 100 + self.next()
            elif kind == "blob":
                e.update(param=self.blob(), big=False)
            elif kind == "pid":
                e["pid"] = self.next()
            elif kind == "dup_same":
                c["scenes"].insert(r.randrange(len(c["scenes"]) + 1), self.scene(s["name"]))
            elif kind == "dup_other":
                r.choice(lib)["scenes"].append(self.scene(s["name"]))
            elif kind == "literal":
                r.choice(lib)["scenes"].append(self.scene(f"{s['name']} ({r.choice(lib)['name']})"))
            elif kind == "exp_cat":
                c["expired"] = not c.get("expired")
            elif kind in ("expired", "rules", "refused", "type5"):
                e[kind] = not e.get(kind)
            elif kind == "rm_scene":
                c["scenes"].remove(s)
            elif kind == "placeholder":
                e.update(param=None, big=True)
            elif kind == "rename_scene":
                s["name"] = r.choice([n for n in NAMES if n != s["name"]])
            else:   # restore
                for flag in ("expired", "rules", "refused", "type5"):
                    e.pop(flag, None)
                c.pop("expired", None)


def history(seed: int) -> list[dict[str, Any]]:
    """Rounds of (library, the SKUs fetched, whether the fetch repeats, the SKUs --resolve runs for)."""
    g = Gen(seed)
    lib = g.library()
    joins = {SKUS[0]: 0} | {s: g.rng.randrange(0, 5) for s in SKUS[1:] if g.rng.random() < 0.7}
    rounds = []
    for i in range(g.rng.randrange(3, 6)):
        for _ in range(g.rng.randrange(1, 4) if i else 0):
            g.edit(lib)
        skus = [s for s in SKUS if joins.get(s, 99) <= i]
        rounds.append({"lib": copy.deepcopy(lib), "skus": skus, "repeat": g.rng.random() < 0.3,
                       "resolve": [s for s in skus if g.rng.random() < 0.25]})
    return rounds


def render(lib: Lib, shuffle: random.Random | None = None) -> bytes:
    def effect(e: dict[str, Any]) -> dict[str, Any]:
        d = {"sceneCode": e["code"], "sceneType": 9 if e.get("refused") else e["stype"],
             "scenceParam": e["param"] or "", "scenceParamId": e["pid"], "cmdVersion": e["cmdv"]}
        if e.get("big"):
            d["bigEffectStr"] = 1
        if e.get("expired"):
            d.update(GONE)
        if e.get("rules"):
            d["rules"] = [{"hardVersion": "1.00.01", "softVersion": "1.05.00"}]
        if e.get("type5"):
            d.update(sceneType=0, specialEffect=[{"supportSku": list(SKUS), "sceneCode": e["code"],
                                                  "sceneType": 5, "scenceParam": P50}])
        if e.get("big5"):   # a sceneType-5 placeholder: listed, and cut once --resolve bakes a 0x50 blob in
            d.update(sceneType=0, specialEffect=[{"supportSku": list(SKUS), "sceneCode": e["code"],
                                                  "sceneType": 5, "scenceParam": "", "bigEffectStr": 1}])
        return d

    cats = []
    for c in lib:
        scenes = [{"sceneId": s["uid"], "sceneName": s["name"],
                   "lightEffects": [effect(e) for e in s["effects"]]} for s in c["scenes"]]
        if shuffle:
            shuffle.shuffle(scenes)
        cats.append({"categoryName": c["name"], **(GONE if c.get("expired") else {}), "scenes": scenes})
    if shuffle:
        shuffle.shuffle(cats)
    return json.dumps({"data": {"categories": cats}}).encode()


@pytest.fixture
def fs(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    spec = importlib.util.spec_from_file_location("_tool_fetch_scenes_naming", TOOLS / "fetch_scenes.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.scene_rows = functools.cache(mod.scene_rows)   # the device table, resolved once
    mod.BODIES = {}

    class Resp(io.BytesIO):
        def __enter__(self) -> Resp:
            return self

        def __exit__(self, *_a: object) -> None:
            return None

    def urlopen(req: Any, timeout: float = 0) -> Resp:
        return Resp(mod.BODIES[urllib.parse.parse_qs(urllib.parse.urlparse(req.full_url).query)["sku"][0]])

    monkeypatch.setattr(mod.urllib.request, "urlopen", urlopen)
    mod.CAP = {}
    in_time, uploads, named = mod._in_time, mod.uploads_type5, mod.stored_names

    def _in_time(sku: str, effects: list[dict[str, Any]], *a: Any) -> bool:
        ok = in_time(sku, effects, *a)
        if not ok:
            mod.CAP["out"].add(effects[0]["scenceParamId"])
        return ok

    def uploads_type5(entry: dict[str, Any], versions: frozenset[int]) -> bool:
        cut = uploads(entry, versions)
        if cut:
            mod.CAP["out"].add(entry.get("param_id"))
        return cut

    def stored_names(listed: list[Any], *a: Any, **k: Any) -> list[str]:
        names = named(listed, *a, **k)
        mod.CAP["named"] = (copy.deepcopy(listed), list(names))
        return names

    mod._in_time, mod.uploads_type5, mod.stored_names = _in_time, uploads_type5, stored_names
    return mod


def _fetch(fs: ModuleType, sku: str, body: bytes, root: Path) -> tuple[dict[str, Any], dict[str, set[str]]]:
    """main() for ``sku`` over the store and tombstone in ``root``: the catalog after, and the names each kind
    of line printed."""
    fs.OUT_DIR, fs.TOMBSTONE, fs.BODIES[sku] = root, root / "tombstone.json", body
    fs.CAP.clear()
    fs.CAP.update(out=set(), named=([], []))
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        assert fs.main([sku]) == 0, out.getvalue()
    lines = out.getvalue().splitlines()
    return fs.read_catalog(sku, root), {k: {n for line in lines if key in line for n in NAME.findall(line)}
                                        for k, key in LINES.items()}


def replay(fs: ModuleType, rounds: list[dict[str, Any]], root: Path,
           shuffle: random.Random | None = None) -> tuple[list[dict[str, Any]], list[str]]:
    """Each catalog after each fetch, and the invariants broken (shuffled: no checks)."""
    bad: list[str] = []
    snaps: list[dict[str, Any]] = []
    owner: dict[int, int] = {}            # param id -> uid
    last: dict[str, set[int]] = {}        # SKU -> uids its previous fetch's library held
    retired: dict[str, dict[str, tuple[int, Any]]] = {}

    def uid(entry: dict[str, Any]) -> int:
        return owner[entry["param_id"]]

    for ri, rnd in enumerate(rounds):
        for c in rnd["lib"]:
            for s in c["scenes"]:
                owner.update((e["pid"], s["uid"]) for e in s["effects"])
        here = {s["uid"] for c in rnd["lib"] for s in c["scenes"]}
        for rep in range(2 if rnd["repeat"] else 1):
            for sku in rnd["skus"]:
                body = render(rnd["lib"], shuffle)
                before = fs.read_catalog(sku, root)
                dropped = set(fs.read_tombstone(root / "tombstone.json").get(sku, {}).get("dropped", ()))
                after, said = _fetch(fs, sku, body, root)
                snaps.append(after)
                if shuffle:
                    continue

                def fail(kind: str, msg: str, sku: str = sku, ri: int = ri) -> None:
                    bad.append(f"{kind} round {ri} {sku}: {msg}")

                listed, names = fs.CAP["named"]
                shown = {owner[entry["param_id"]] for _own, entry, _codes in listed}   # the scenes listed
                kept = {n for (_own, entry, _codes), n in zip(listed, names, strict=True)
                        if entry["param_id"] not in fs.CAP["out"]}   # all but the ones main() cuts
                if len(set(names)) != len(names) or not kept <= set(after):
                    fail("UNIQ", f"{names} -> {sorted(after)}")
                for n in set(before) & set(after):
                    u0, u1 = uid(before[n]), uid(after[n])
                    moved = fs._scene(before[n]) != fs._scene(after[n])
                    if moved and n not in said["keep"] | said["guess"]:
                        fail("INV", f"{n!r} {fs._scene(before[n])} -> {fs._scene(after[n])} unprinted")
                    if u0 != u1 and (n not in said["guess"] or u0 not in last.get(sku, ()) or u0 in shown):
                        fail("INV", f"{n!r} moved from scene {u0} to {u1}")
                out = {owner[pid] for pid in fs.CAP["out"]}   # the scenes refused for good or cut
                for n in set(before) - set(after):
                    if n not in said["removed"] or uid(before[n]) not in out:
                        fail("KEEP", f"{n!r} (scene {uid(before[n])}) removed")
                    retired.setdefault(sku, {})[n] = (uid(before[n]), before[n])
                lost = set(retired.get(sku, {})) - set(fs.read_tombstone(root / "tombstone.json").get(sku, {})
                                                       .get("retired", {}))
                if lost:   # retirement is permanent, a reclaim too
                    fail("RETIRE", f"{sorted(lost)} left the tombstone")
                for n in set(after) - set(before):
                    if n in retired.get(sku, {}):
                        u, was = retired[sku][n]
                        if uid(after[n]) != u or n not in said["back"] or any(
                                after[n].get(k) != was.get(k) for k in ("code", "category", "param")):
                            fail("RETIRE", f"{n!r} of scene {u} {fs._scene(was)} back on {uid(after[n])}")
                for (own, entry, codes), n in zip(listed, names, strict=True):
                    pid = entry.get("param_id")
                    olds = [b for b in before.values()
                            if b.get("param_id") == pid and not b.get("placeholder")]
                    if entry.get("placeholder") and len(olds) == 1 and olds[0]["param"] == _resolved(pid) \
                            and after[n]["param"] != olds[0]["param"]:
                        fail("RESOLVED", f"{n!r} lost the blob of param id {pid}")
                    blobs = {entry["param"], _resolved(pid) if entry.get("placeholder") else entry["param"]}
                    for m, (u, was) in retired.get(sku, {}).items():   # an exact return reclaims its name
                        if m != n and m not in after and u == owner[pid] and fs.scene_name(m, was) == own \
                                and fs._scene(was) == fs._scene(entry) and was["param"] in blobs \
                                and (was["param"] or not entry.get("placeholder")
                                     or was.get("param_id") == pid):   # a placeholder: by its param id
                            fail("RETIRE", f"{m!r} of scene {u} not reclaimed by its exact return: {n!r}")
                    exact = [m for m, b in before.items() if fs.scene_name(m, b) == own and _same(b, entry)]
                    twins = [e for o, e, _c in listed if o == own and _same(e, entry)]
                    if len(exact) == 1 and len(twins) == 1 and n != exact[0]:
                        fail("STAY", f"{exact[0]!r}, its code, category, blob and param id, left for {n!r}")
                    holders = [m for m, b in before.items() if uid(b) == owner[pid]]
                    if len(holders) == 1 and holders[0] not in dropped:
                        b = before[holders[0]]
                        if (fs.scene_name(holders[0], b) == own and b["category"] == entry["category"]
                                and b["code"] in codes - {entry["code"]} and n != holders[0]):
                            fail("FOLLOW", f"{holders[0]!r} left its scene {owner[pid]} to {n!r}")
                if rep and (after != before or any(said.values())):
                    fail("IDEM", f"{ {k: v for k, v in said.items() if v} }")
                if not before and sku not in last:
                    alone, _said = _fetch(fs, sku, body, root.parent / f"{root.name}-{sku}-{ri}")
                    fs.OUT_DIR, fs.TOMBSTONE = root, root / "tombstone.json"
                    if alone != after:
                        fail("SAME", f"{sorted(after)} here, {sorted(alone)} alone")
                last[sku] = here
        for sku in rnd["resolve"]:   # --resolve, offline: the blob is the param id's
            catalog = fs.read_catalog(sku, root)
            for e in catalog.values():
                if e.get("placeholder") and e.get("param_id"):
                    e.update(param=_resolved(e["param_id"], e.get("scene_type")))
                    e.pop("placeholder")
            if catalog:
                fs.store_catalog(sku, catalog, root)
    return snaps, bad


def check(fs: ModuleType, rounds: list[dict[str, Any]], root: Path, seed: int) -> list[str]:
    snaps, bad = replay(fs, rounds, root / "ordered")
    shuffled, _ = replay(fs, rounds, root / "shuffled", random.Random(seed))  # noqa: S311 - a reproducible order
    if shuffled != snaps:
        bad.append("DET: a shuffled response stores another catalog")
    return bad


def test_random_histories_keep_every_name_on_its_scene(fs: ModuleType, tmp_path: Path) -> None:
    bad = {seed: v for seed in range(200) if (v := check(fs, history(seed), tmp_path / str(seed), seed))}
    assert not bad, "\n".join(f"seed {seed}: {v[0]}" for seed, v in list(bad.items())[:10])


def _lib(*cats: tuple[str, list[tuple[int, str, list[dict[str, Any]]]]]) -> Lib:
    return [{"name": c, "scenes": [{"uid": u, "name": n, "effects": e} for u, n, e in scenes]}
            for c, scenes in cats]


def _e(code: int, pid: int, param: str | None = "kwEB", **kw: Any) -> dict[str, Any]:
    return {"code": code, "pid": pid, "param": param, "stype": 1, "cmdv": 0, **kw}


def _rounds(*libs: Lib, skus: tuple[str, ...] = ("H6641",), resolve: int = -1) -> list[dict[str, Any]]:
    return [{"lib": lib, "skus": list(skus), "repeat": True, "resolve": list(skus) if i == resolve else []}
            for i, lib in enumerate(libs)]


_X50 = _e(50, 1, "kwUF", stype=2, cmdv=1)   # H6641 plays it before code 40
_DUSK5 = _e(30, 2, None, big5=True)   # a sceneType-5 placeholder
_FIXED = {
    # N1: Rain refused for good, then a new Rain; the old one comes back exactly
    "retired": _rounds(_lib(("Party", [(1, "Snow", [_e(5, 1)]), (2, "Rain", [_e(10, 2)])])),
                       _lib(("Party", [(1, "Snow", [_e(5, 1)]), (2, "Rain", [_e(10, 2, refused=True)])])),
                       _lib(("Party", [(1, "Snow", [_e(5, 1)]), (2, "Rain", [_e(10, 2, refused=True)])]),
                            ("Life", [(3, "Rain", [_e(20, 3, "kwIC")])])),
                       _lib(("Party", [(1, "Snow", [_e(5, 1)]), (2, "Rain", [_e(10, 2)])]),
                            ("Life", [(3, "Rain", [_e(20, 3, "kwIC")])]))),
    # N1: refused Rain back with a new blob, and refused Dusk gone as a new Dusk comes: neither is its scene's
    # exact return, so neither takes a retired name
    "retired, not exactly": _rounds(
        _lib(("Party", [(1, "Snow", [_e(5, 1)]), (2, "Rain", [_e(10, 2, "kwUF")]),
                        (4, "Dusk", [_e(40, 4, "kwQE")])])),
        _lib(("Party", [(1, "Snow", [_e(5, 1)]), (2, "Rain", [_e(10, 2, "kwUF", refused=True)]),
                        (4, "Dusk", [_e(40, 4, "kwQE", refused=True)])])),
        _lib(("Party", [(1, "Snow", [_e(5, 1)]), (2, "Rain", [_e(10, 2, "kwIC")]),
                        (5, "Dusk", [_e(50, 5, "kwMD")])]))),
    # N1 / N7: a resolved placeholder refused for good, then back as the same placeholder: its name, its blob
    "retired resolved": _rounds(*(_lib(("Party", [(1, "Snow", [_e(5, 1)]),
                                                  (2, "Dusk", [_e(30, 2, None, big=True, refused=out)])]))
                                  for out in (False, True, False)), resolve=0),
    # N2: Christmas dropped, then a new Christmas of its category
    "dropped": _rounds(_lib(("Festival", [(1, "Christmas", [_e(10, 1)]), (2, "Snow", [_e(12, 2)])])),
                       _lib(("Festival", [(2, "Snow", [_e(12, 2)])])),
                       _lib(("Festival", [(2, "Snow", [_e(12, 2)]), (3, "Christmas", [_e(99, 3, "k5mZ")])]))),
    # N3: Glow renamed Aurora as a new Glow comes: the old name stays on its scene, still there as Aurora
    "renamed": _rounds(_lib(("Party", [(1, "Glow", [_e(10, 1, None)])])),
                       _lib(("Party", [(1, "Aurora", [_e(10, 1, None)]), (2, "Glow", [_e(20, 2, None)])]))),
    # N3: X's sibling dropped as its first effect expires: X plays another of its own effects
    "own effect": _rounds(_lib(("Party", [(1, "X", [_X50, _e(40, 2, "kwQE")]),
                                          (2, "X", [_e(20, 3, "kwIC")])])),
                          _lib(("Party", [(1, "X", [{**_X50, "expired": True}, _e(40, 2, "kwQE")])]))),
    # N3 / N4: one of two same-code siblings dropped; the other is still listed
    "sibling": _rounds(_lib(("Party", [(1, "Glow", [_e(30, 1, "AAAA")]), (2, "Glow", [_e(30, 2, "BBBB")])])),
                       _lib(("Party", [(1, "Glow", [_e(30, 1, "AAAA")])]))),
    # N2: a stored Glow renumbered (a new param id too) as another Glow comes: either could be it, so neither
    # takes its name
    "ambiguous": _rounds(_lib(("Party", [(1, "Glow", [_e(10, 1, None)]), (2, "Glow", [_e(20, 2, None)])])),
                         _lib(("Party", [(3, "Glow", [_e(30, 3, None)]), (1, "Glow", [_e(11, 4, None)]),
                                         (2, "Glow", [_e(20, 2, None)])]))),
    # N5: same-code siblings that differ only in param id, fetched twice
    "tie": _rounds(_lib(("Party", [(1, "Glow", [_e(30, 1, None)]), (2, "Glow", [_e(30, 2, None)])]))),
    # N6: H6641 names Flash 7 first; H6047 first fetches both
    "history": [*_rounds(_lib(("Party", [(1, "Flash", [_e(7, 1)])])),
                         _lib(("Party", [(1, "Flash", [_e(7, 1)]), (2, "Flash", [_e(9, 2, "kwkJ")])]))),
                {"lib": _lib(("Party", [(1, "Flash", [_e(7, 1)]), (2, "Flash", [_e(9, 2, "kwkJ")])])),
                 "skus": ["H6641", "H6047"], "repeat": False, "resolve": []}],
    # N7: a resolved placeholder renumbered as its category is renamed takes a new name, and its blob
    "resolved": _rounds(_lib(("Party", [(1, "Dusk", [_e(30, 7, None, big=True)])])),
                        _lib(("Life", [(1, "Dusk", [_e(31, 7, None, big=True)])])), resolve=0),
    # N2: one Glow renumbered (new blob and param id) as the other moves to Life: both stored Glows fit it,
    # so it takes a new name, not the lowest
    "ambiguous stored": _rounds(_lib(("Party", [(1, "Glow", [_e(10, 1)]), (2, "Glow", [_e(20, 2, "kwIC")])])),
                                _lib(("Party", [(1, "Glow", [_e(11, 3, "kwMD")])]),
                                     ("Life", [(2, "Glow", [_e(22, 4, "kwQE")])]))),
    # N2: the stored Glows an ambiguous scene fits stay out of every later match
    "ambiguous held": _rounds(_lib(("Party", [(1, "Glow", [_e(10, 1)]), (2, "Glow", [_e(20, 2)])])),
                              _lib(("Party", [(1, "Glow", [_e(11, 3)]), (2, "Glow", [_e(22, 2, "kwIC")]),
                                              (4, "Glow", [_e(40, 4, "kwMD")])]))),
    # N2: a bare Glow leaves as a new bare Glow comes: a guess by category alone, printed as one
    "bare": _rounds(_lib(("Party", [(1, "Glow", [_e(10, 1, None)])])),
                    _lib(("Party", [(2, "Glow", [_e(20, 2, None)])]))),
    # N5: same-code siblings both take a new blob and param id: either could be either, so neither is swapped
    "swap": _rounds(_lib(("Party", [(1, "Glow", [_e(30, 1)]), (2, "Glow", [_e(30, 2, "kwIC")])])),
                    _lib(("Party", [(1, "Glow", [_e(30, 3, "kwMD")]), (2, "Glow", [_e(30, 4, "kwQE")])]))),
    # N5: same-code siblings that differ only in param id both turn RGBIC: each keeps its name by param id
    "param id": _rounds(_lib(("Party", [(1, "Glow", [_e(30, 1)]), (2, "Glow", [_e(30, 2)])])),
                        _lib(("Party", [(1, "Glow", [_e(30, 1, stype=2, cmdv=1)]),
                                        (2, "Glow", [_e(30, 2, stype=2, cmdv=1)])]))),
    # N5: two identical scenes of one name, fetched twice: alike, so each keeps its name
    "twins": _rounds(_lib(("Party", [(1, "Glow", [_e(30, 1)]), (2, "Glow", [_e(30, 1)])]))),
    # N5 / N7: same-code placeholder siblings, resolved; one's param id reissued, then it is renumbered:
    # the other keeps its name and blob, and no identical re-fetch adds a name
    "resolved siblings": _rounds(
        _lib(("Party", [(1, "Glow", [_e(30, 1, None, big=True)]), (2, "Glow", [_e(30, 2, None, big=True)])])),
        _lib(("Party", [(1, "Glow", [_e(30, 3, None, big=True)]), (2, "Glow", [_e(30, 2, None, big=True)])])),
        _lib(("Party", [(1, "Glow", [_e(40, 3, None, big=True)]), (2, "Glow", [_e(30, 2, None, big=True)])])),
        resolve=0),
    # N5: as that, and the other's cmdVersion changes as it is renumbered: a placeholder has no blob to match
    # its sibling's placeholder by, so it keeps its own name by its param id
    "resolved siblings, a new cmdVersion": _rounds(
        _lib(("Party", [(1, "Glow", [_e(30, 1, None, big=True)]), (2, "Glow", [_e(30, 2, None, big=True)])])),
        _lib(("Party", [(1, "Glow", [_e(30, 3, None, big=True)]), (2, "Glow", [_e(30, 2, None, big=True)])])),
        _lib(("Party", [(1, "Glow", [_e(40, 3, None, big=True)]),
                        (2, "Glow", [_e(30, 2, None, big=True, cmdv=1)])])),
        resolve=0),
    # N5 / N7: two identical placeholders, resolved, fetched again: alike, so each keeps its name and blob
    "resolved twins": _rounds(*(_lib(("Party", [(1, "Glow", [_e(30, 1, None, big=True)]),
                                                (2, "Glow", [_e(30, 1, None, big=True)])]))
                                for _ in range(2)), resolve=0),
    # N1: a sceneType-5 placeholder whose blob --resolve bakes in is a new-DIY one, cut and retired: listed
    # again, it is cut again, so no reclaim is printed
    "type5 resolved": _rounds(*(_lib(("Party", [(1, "Snow", [_e(5, 1)]), (2, "Dusk", [_DUSK5])]))
                                for _ in range(2)), resolve=0),
    # N7: that one renumbered as its category is renamed takes a new name, which keeps its placeholder: the
    # resolved blob it would take by its param id is cut
    "type5 resolved, renumbered": _rounds(
        _lib(("Party", [(1, "Snow", [_e(5, 1)])]), ("Zootopia 2", [(2, "Dusk", [_DUSK5])])),
        _lib(("Party", [(1, "Snow", [_e(5, 1)])]), ("Zootopia", [(2, "Dusk", [{**_DUSK5, "code": 31}])])),
        resolve=0),
}


@pytest.mark.parametrize("case", list(_FIXED))
def test_fixed_histories_keep_every_name_on_its_scene(case: str, fs: ModuleType, tmp_path: Path) -> None:
    assert check(fs, _FIXED[case], tmp_path, 0) == []

