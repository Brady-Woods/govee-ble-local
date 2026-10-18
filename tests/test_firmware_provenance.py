"""Where a device has a spec/firmware.yaml image, its frames and adverts come from that image (S7a).

Two checks hold that:

* at run time, fake_ble.Provenance (an autouse fixture in tests/conftest.py) fails any test in which a
  Device whose SKU has an image reads a frame or advert the image didn't send;
* statically, the simulator (tests/firmware_sim.py and fake_ble.FirmwareGovee) builds no bytes
  outside the spec classes.
"""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from govee_ble_local import create_device
from govee_ble_local.devices.device import Device
from govee_ble_local.transport import connection as conn_mod

from fake_ble import FakeGovee, FirmwareGovee, Provenance, ble_device, frame

TESTS = Path(__file__).resolve().parent
_PACKERS = {"to_bytes", "from_bytes", "pack", "pack_into", "ljust", "rjust", "zfill"}
_HAND_FRAMES = {"frame", "_frame", "status_burst"}
_FROM_INTS = (ast.Constant, ast.List, ast.Tuple, ast.Set, ast.Dict, ast.ListComp, ast.SetComp,
              ast.GeneratorExp, ast.BinOp, ast.UnaryOp, ast.Call)


def _bytes_valued(e: ast.AST) -> bool:
    """An expression that is plainly bytes: a literal, a bytes()/bytearray() call, a slice, an encode()."""
    if isinstance(e, ast.Constant):
        return isinstance(e.value, bytes)
    if isinstance(e, ast.Subscript):
        return isinstance(e.slice, ast.Slice)
    if isinstance(e, ast.Call):
        fn = e.func
        return (isinstance(fn, ast.Name) and fn.id in ("bytes", "bytearray")) or (
            isinstance(fn, ast.Attribute) and fn.attr == "encode")
    return False


def _concatenates(n: ast.AST) -> bool:
    if isinstance(n, ast.BinOp) and isinstance(n.op, (ast.Add, ast.Mult)):
        return _bytes_valued(n.left) or _bytes_valued(n.right)
    return isinstance(n, ast.AugAssign) and isinstance(n.op, ast.Add) and _bytes_valued(n.value)


def built_bytes(tree: ast.AST) -> list[tuple[int, str]]:
    """Each place under ``tree`` that builds bytes by hand, as (line, what): a non-empty bytes literal;
    bytes()/bytearray() from ints (an int, a display, a comprehension, arithmetic or a call: anything
    but a value to convert); to_bytes / from_bytes / struct / padding; a list or tuple of ints;
    concatenation of bytes; a slice write; a join on bytes; a hand-frame helper."""
    found: list[tuple[int, str]] = []
    for n in ast.walk(tree):
        line = getattr(n, "lineno", 0)
        if isinstance(n, ast.Constant) and isinstance(n.value, bytes) and n.value:
            found.append((line, "a bytes literal"))
        elif isinstance(n, ast.Call):
            fn = n.func
            if isinstance(fn, ast.Name) and fn.id in ("bytes", "bytearray") and (
                    not n.args or any(isinstance(a, _FROM_INTS) for a in n.args)):
                found.append((line, f"{fn.id}() from ints"))
            elif isinstance(fn, ast.Name) and fn.id in _HAND_FRAMES:
                found.append((line, f"the hand-frame helper {fn.id}()"))
            elif isinstance(fn, ast.Attribute) and (fn.attr in _PACKERS or (fn.attr == "join" and (
                    isinstance(fn.value, ast.Constant) and isinstance(fn.value.value, bytes)))):
                found.append((line, f".{fn.attr}()"))
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            mods = [a.name for a in n.names] if isinstance(n, ast.Import) else [n.module or ""]
            if {"struct", "array"} & {m.split(".")[0] for m in mods}:
                found.append((line, "struct / array"))
        elif isinstance(n, (ast.List, ast.Tuple, ast.Set)) and n.elts and all(
                isinstance(e, ast.Constant) and type(e.value) is int for e in n.elts):
            found.append((line, "a list of ints"))
        elif _concatenates(n):
            found.append((line, "bytes concatenation"))
        elif isinstance(n, (ast.Assign, ast.AugAssign)) and any(
                isinstance(t, ast.Subscript) and isinstance(t.slice, ast.Slice)
                for t in (n.targets if isinstance(n, ast.Assign) else [n.target])):
            found.append((line, "a slice write"))
    return found


def _simulator() -> list[tuple[str, ast.AST]]:
    fake = ast.parse((TESTS / "fake_ble.py").read_text())
    image = [c for c in fake.body if isinstance(c, ast.ClassDef) and c.name == "FirmwareGovee"]
    assert len(image) == 1
    return [("firmware_sim.py", ast.parse((TESTS / "firmware_sim.py").read_text())),
            ("fake_ble.py", image[0])]


def test_the_simulator_builds_no_bytes_outside_the_spec_classes() -> None:
    hand_built = ["b'\\xaa\\x01'", "bytes([0xAA, 1])", "bytearray([1, 2])", "bytes(20)", "bytes(n + 1)",
                  "bytes(x for x in y)", "bytes(range(4))", "bytes()", "n.to_bytes(2, 'little')",
                  "int.from_bytes(b, 'big')", "struct.pack('<H', 1)", "import struct", "[0xAA, 0x01]",
                  "(1, 2)", "a[:2] + b", "x + bytes(y)", "buf += data[1:]", "buf[0:2] = x",
                  "b''.join(parts)", "frame(0xAA, 1)", "status_burst(t)", "v.ljust(16, z)",
                  "s.encode() + t", "z * bytes(y)"]
    for src in hand_built:
        assert built_bytes(ast.parse(src)), src
    through_the_spec = ["bytes(data)", "bytes(a['contents'])", "v.encode('ascii')", "request[3:19]",
                        "_spec.emit(f, 20)", "len(given) + short", "b''", "now + ms", "'a' + b",
                        "('raw', obj, 0)"]
    for src in through_the_spec:
        assert built_bytes(ast.parse(src)) == [], src
    found = [f"{name}:{line}: {what}" for name, tree in _simulator() for line, what in built_bytes(tree)]
    assert found == [], "build these through the spec classes:\n" + "\n".join(found)


async def test_a_device_with_a_firmware_image_reads_only_what_the_image_sent(
    firmware_provenance: Provenance, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(conn_mod, "WRITE_ACK_TIMEOUT", 0.1)   # an unpaired H5083 leaves 33 b5 unanswered
    found = firmware_provenance.violations

    def device(peripheral: Any, sku: str = "H5083") -> Device:
        monkeypatch.setattr(conn_mod, "establish_connection", peripheral.establish)
        dev = create_device(ble_device("AA:BB:CC:DD:EE:84", f"ihoment_{sku}_0084"), sku)
        dev._conn._idle_disconnect = 0
        return dev

    # Its image's frames and advert are what it reads.
    plug = FirmwareGovee()
    dev = device(plug)
    await dev.update()
    dev.ingest_advertisement(plug.advertisement())  # type: ignore[arg-type]
    assert found == []
    # A FakeGovee answering an H5083: its handshake reply and reads are built by hand.
    await device(FakeGovee()).update()
    assert found
    found.clear()
    # A mocked query that returns a hand-built reply.
    dev._conn.query = AsyncMock(return_value=[frame(0xAA, 0x01, 0x01)])  # type: ignore[method-assign]
    await dev._read_plug()
    assert found
    found.clear()
    # A hand-built advert.
    dev.ingest_advertisement(SimpleNamespace(manufacturer_data={0x8843: bytes([0xEC, 0, 2, 2, 1])}))  # type: ignore[arg-type]
    assert found
    found.clear()
    # A SKU with no image is not held to it.
    await device(FakeGovee(), "H5080").update()
    assert found == []
