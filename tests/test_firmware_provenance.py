"""Where a device has a spec/firmware.yaml image, its frames and adverts come from that image (S7a).

Two checks hold that:

* at run time, fake_ble.Provenance (an autouse fixture in tests/conftest.py) fails any test in which a
  Device whose SKU has an image, or an advert read for that SKU, gets a frame or advert the image didn't
  build and send it;
* statically, the simulator (tests/firmware_sim.py, fake_ble.FirmwareGovee and the fake_ble helpers it
  calls) builds no bytes outside the spec classes.
"""
from __future__ import annotations

import ast
import asyncio
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from govee_ble_local import create_device, scanner
from govee_ble_local.devices.device import Device
from govee_ble_local.devices.profile import profile_for
from govee_ble_local.identify import identify
from govee_ble_local.transport import connection as conn_mod

from fake_ble import FakeGovee, FirmwareGovee, Provenance, ble_device, frame

TESTS = Path(__file__).resolve().parent
_CONVERTER = "_image_bytes"   # the simulator's one conversion of an image value to bytes
_CONVERTERS = {"to_bytes", "from_bytes", "fromhex", "pack", "pack_into", "unhexlify", "a2b_hex", "b16decode",
               "b32decode", "b64decode", "ljust", "rjust", "zfill", "center", "translate"}
_HAND_FRAMES = {"frame", "_frame", "status_burst"}
_MUTATORS = {"append", "extend", "insert"}
_IMPORTS_OK = {"__future__", "asyncio", "collections.abc", "copy", "dataclasses", "functools", "importlib",
               "pathlib", "random", "re", "types", "typing", "yaml", "kaitaistruct", "firmware_sim"}
_SPEC_SIDE = {"govee_ble_local.wire._spec", "govee_ble_local.crypto", "govee_ble_local.const",
              "govee_ble_local.exceptions"}


def _ints(e: ast.AST) -> bool:
    """An int literal, a display of int literals, or arithmetic on them."""
    if isinstance(e, ast.Constant):
        return type(e.value) is int
    if isinstance(e, (ast.List, ast.Tuple, ast.Set)):
        return bool(e.elts) and all(_ints(x) for x in e.elts)
    if isinstance(e, ast.BinOp):
        return _ints(e.left) and _ints(e.right)
    return isinstance(e, ast.UnaryOp) and _ints(e.operand)


def _bytes_valued(e: ast.AST) -> bool:
    """An expression that is plainly bytes: a literal, a conversion call, a slice."""
    if isinstance(e, ast.Constant):
        return isinstance(e.value, bytes)
    if isinstance(e, ast.Subscript):
        return isinstance(e.slice, ast.Slice)
    if isinstance(e, ast.Call):
        fn = e.func
        return (isinstance(fn, ast.Name) and fn.id in ("bytes", "bytearray", _CONVERTER)) or (
            isinstance(fn, ast.Attribute) and fn.attr == "encode")
    return False


def _import_ok(module: str, name: str | None = None) -> bool:
    full = f"{module}.{name}" if name else module
    return module in _IMPORTS_OK or module in _SPEC_SIDE or full in _SPEC_SIDE


class _Builds(ast.NodeVisitor):
    """The places under a tree that build bytes by hand (see :func:`built_bytes`)."""

    def __init__(self) -> None:
        self.found: list[tuple[int, str]] = []
        self._in: list[str] = []

    def _add(self, n: ast.AST, what: str) -> None:
        self.found.append((getattr(n, "lineno", 0), what))

    def visit_FunctionDef(self, n: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self._in.append(n.name)
        self.generic_visit(n)
        self._in.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Constant(self, n: ast.Constant) -> None:
        if isinstance(n.value, bytes) and n.value:
            self._add(n, "a bytes literal")

    def _call(self, n: ast.Call) -> str | None:
        fn = n.func
        name = fn.id if isinstance(fn, ast.Name) else fn.attr if isinstance(fn, ast.Attribute) else ""
        converting = self._in[-1:] == [_CONVERTER]
        if isinstance(fn, ast.Name) and name in ("bytearray", "memoryview"):
            return f"{name}()"
        if isinstance(fn, ast.Name) and name == "bytes" and not converting:
            return "bytes() outside the one conversion"
        if isinstance(fn, ast.Attribute) and name == "encode" and not converting:
            return ".encode() outside the one conversion"
        if isinstance(fn, ast.Name) and name in _HAND_FRAMES:
            return f"the hand-frame helper {name}()"
        if isinstance(fn, ast.Attribute) and (name in _CONVERTERS or (
                name == "decode" and isinstance(fn.value, ast.Name) and fn.value.id == "codecs") or (
                name == "join" and isinstance(fn.value, ast.Constant) and isinstance(fn.value.value, bytes))):
            return f".{name}()"
        if isinstance(fn, ast.Attribute) and name in _MUTATORS and any(_ints(a) for a in n.args):
            return f".{name}() of ints"
        return None

    def visit_Call(self, n: ast.Call) -> None:
        what = self._call(n)
        if what is not None:
            self._add(n, what)
        self.generic_visit(n)

    def visit_Import(self, n: ast.Import) -> None:
        for a in n.names:
            if not _import_ok(a.name):
                self._add(n, f"import {a.name}")

    def visit_ImportFrom(self, n: ast.ImportFrom) -> None:
        for a in n.names:
            if not _import_ok(n.module or "", a.name):
                self._add(n, f"from {n.module} import {a.name}")

    def _targets(self, n: ast.AST, targets: list[ast.expr], value: ast.AST) -> None:
        for t in targets:
            if isinstance(t, ast.Subscript) and isinstance(t.slice, ast.Slice):
                self._add(n, "a slice write")
            elif isinstance(t, ast.Subscript) and _ints(value):
                self._add(n, "an index write of ints")

    def visit_Assign(self, n: ast.Assign) -> None:
        self._targets(n, n.targets, n.value)
        self.generic_visit(n)

    def visit_AugAssign(self, n: ast.AugAssign) -> None:
        self._targets(n, [n.target], n.value)
        if isinstance(n.op, ast.Add) and _bytes_valued(n.value):
            self._add(n, "bytes concatenation")
        self.generic_visit(n)

    def visit_BinOp(self, n: ast.BinOp) -> None:
        if isinstance(n.op, (ast.Add, ast.Mult)) and (_bytes_valued(n.left) or _bytes_valued(n.right)):
            self._add(n, "bytes concatenation")
        self.generic_visit(n)

    def _display(self, n: ast.List | ast.Tuple | ast.Set) -> None:
        if _ints(n):
            self._add(n, "a list of ints")
        self.generic_visit(n)

    visit_List = visit_Tuple = visit_Set = _display


def built_bytes(tree: ast.AST) -> list[tuple[int, str]]:
    """Each place under ``tree`` that builds bytes by hand, as (line, what):

    * a non-empty bytes literal, ``bytearray()`` / ``memoryview()``, or ``bytes()`` / ``.encode()`` outside
      ``_image_bytes`` (the one conversion of a value the image or a .ksy gives);
    * ``to_bytes`` / ``from_bytes`` / ``fromhex`` / ``struct`` / ``binascii`` / ``codecs.decode`` /
      ``base64`` / padding, or ``b"".join``;
    * a list or tuple of ints, ``append`` / ``extend`` / ``insert`` of ints, an index write of ints, a
      slice write, or bytes concatenation;
    * a hand-frame helper (``frame``, ``_frame``, ``status_burst``);
    * an import of anything but the standard modules it needs, PyYAML, kaitaistruct, firmware_sim and
      the spec side of the library (``wire._spec``, ``crypto``, ``const``, ``exceptions``)."""
    v = _Builds()
    v.visit(tree)
    return v.found


def _simulator() -> list[tuple[str, ast.AST]]:
    """firmware_sim.py, FirmwareGovee, and the fake_ble functions FirmwareGovee calls (followed through
    the functions they call). A name FirmwareGovee takes from fake_ble's imports must be an import the
    rule allows."""
    fake = ast.parse((TESTS / "fake_ble.py").read_text())
    functions = {n.name: n for n in fake.body if isinstance(n, ast.FunctionDef)}
    imported: dict[str, ast.stmt] = {}
    for n in fake.body:
        if isinstance(n, (ast.Import, ast.ImportFrom)):
            for a in n.names:
                imported[(a.asname or a.name).split(".")[0]] = n
    image = next(c for c in fake.body if isinstance(c, ast.ClassDef) and c.name == "FirmwareGovee")
    sim = ast.parse((TESTS / "firmware_sim.py").read_text())
    parts: list[tuple[str, ast.AST]] = [("firmware_sim.py", sim), ("fake_ble.py FirmwareGovee", image)]
    todo: list[ast.AST] = [image]
    seen: set[str] = set()
    while todo:
        tree = todo.pop()
        called = {n.func.id for n in ast.walk(tree)
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        for name in sorted((called & set(functions)) - seen):
            seen.add(name)
            parts.append((f"fake_ble.py {name}", functions[name]))
            todo.append(functions[name])
        for name in sorted((names & set(imported)) - seen):
            seen.add(name)
            parts.append((f"fake_ble.py import of {name}", imported[name]))
    return parts


def test_the_simulator_builds_no_bytes_outside_the_spec_classes() -> None:
    hand_built = [
        "b'\\xaa\\x01'", "bytes([0xAA, 1])", "bytes(v)", "bytearray(20)", "memoryview(b)", "bytes(20)",
        "n.to_bytes(2, 'little')", "int.from_bytes(b, 'big')", "bytes.fromhex('aa01')",
        "bytearray.fromhex(x)",
        "struct.pack('<H', 1)", "import struct", "import binascii", "binascii.unhexlify(h)",
        "codecs.decode(h, 'hex')", "base64.b16decode(h)", "'\\xaa'.encode('latin-1')", "[0xAA, 0x01]",
        "(1, 2)", "a[:2] + b", "x + _image_bytes(y)", "buf += data[1:]", "buf[0:2] = x", "buf[0] = 0xAA",
        "lst.append(0xAA)", "lst.extend([1, 2])", "lst.insert(0, 0x33)", "b''.join(parts)",
        "frame(0xAA, 1)", "status_burst(t)", "v.ljust(16, z)", "from fake_ble import frame",
        "from govee_ble_local.wire import build", "import conftest",
    ]
    for src in hand_built:
        assert built_bytes(ast.parse(src)), src
    through_the_spec = [
        "def _image_bytes(v):\n    return v.encode('ascii') if isinstance(v, str) else bytes(v)",
        "request[3:19]", "_spec.emit(f, 20)", "len(given) + short", "b''", "now + ms", "'a' + b",
        "('raw', obj, 0)", "self.state[k] = v", "fills.append(e)", "isinstance(v, bytearray)",
        "from govee_ble_local.wire import _spec", "from govee_ble_local import crypto", "import yaml",
    ]
    for src in through_the_spec:
        assert built_bytes(ast.parse(src)) == [], src
    found = [f"{name}:{line}: {what}" for name, tree in _simulator() for line, what in built_bytes(tree)]
    assert found == [], "build these through the spec classes:\n" + "\n".join(found)


async def test_a_device_with_a_firmware_image_reads_only_what_the_image_sent(
    firmware_provenance: Provenance, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)   # reads made to log a frame (the masked 33 b2 / aa b1) are not checked
    monkeypatch.setattr(conn_mod, "WRITE_ACK_TIMEOUT", 0.1)   # an unpaired H5083 leaves 33 b5 unanswered
    found = firmware_provenance.violations
    ble = ble_device("AA:BB:CC:DD:EE:84", "ihoment_H5083_0084")

    def device(peripheral: Any, sku: str = "H5083", make: Any = None) -> Device:
        monkeypatch.setattr(conn_mod, "establish_connection", peripheral.establish)
        dev = make() if make is not None else create_device(ble, sku)
        dev._conn._idle_disconnect = 0
        return dev

    def caught() -> bool:
        hit = bool(found)
        found.clear()
        return hit

    # The image's own frames and adverts; another company's entry beside the advert isn't checked.
    plug = FirmwareGovee()
    adv = plug.advertisement()
    assert adv is not None
    assert identify(adv.local_name, {0x004C: bytes([2, 21]), **adv.manufacturer_data}) is not None
    dev = device(plug)
    await dev.update()
    dev.ingest_advertisement(adv)  # type: ignore[arg-type]
    assert found == []
    # A hand-built advert, through the Device, identify() or the scanner.
    hand = {0x8843: bytes([0xEC, 0, 2, 2, 1])}
    dev.ingest_advertisement(SimpleNamespace(manufacturer_data=hand))  # type: ignore[arg-type]
    assert caught()
    identify("ihoment_H5083_0084", hand)
    assert caught()
    assert scanner.supported("ihoment_H5083_0084", hand) and caught()
    # A frame handed to sim.send: delivered, but the image never built it.
    plug.sim.send("laundered", frame(0xAA, 0x01, 0x01))
    await asyncio.sleep(0.02)
    assert caught()
    # A mocked query's reply, and a genuine frame of the last link replayed on the new one.
    stale = next(p for what, p in plug.sim.sent if what == "reply e7_session_key")
    await dev._conn.disconnect()
    await dev.update()
    for reply in (frame(0xAA, 0x01, 0x01), stale):
        dev._conn.query = AsyncMock(return_value=[reply])  # type: ignore[method-assign]
        await dev._read_plug()
        assert caught(), reply.hex()
    # A FakeGovee answering an H5083: through the Device, its connection alone, a Device built from the
    # H5083 profile without sku=, and a Device subclass.
    await device(FakeGovee()).update()
    assert caught()
    await device(FakeGovee())._conn.connect()
    assert caught()
    await device(FakeGovee(), make=lambda: Device(profile_for("H5083"), ble)).update()  # type: ignore[arg-type]
    assert caught()

    class Wrapped(Device):
        async def update(self) -> Any:
            return await super().update()

    await device(FakeGovee(), make=lambda: Wrapped(profile_for("H5083"), ble, sku="H5083")).update()  # type: ignore[arg-type]
    assert caught()
    # A SKU with no image is not held to it.
    await device(FakeGovee(), "H5080").update()
    assert found == []
