"""No hand-built frames where a device has a firmware image (S7a): the simulator and the H5083 scenarios.

The simulator (tests/firmware_sim.py, fake_ble.FirmwareGovee) builds every frame and advert through the
spec, and every test that drives an H5083 takes its frames from it or from the library's builders.
This lint fails on a frame-shaped literal there:

* a call to a hand-frame helper (``frame``, ``_frame``, ``status_burst``);
* a bytes literal, or ``bytes([...])`` / ``bytearray([...])`` led by an int literal, that starts with a
  frame header (the spec's ``pro_type`` values) or with the Govee manufacturer-data marker;
* a hex string of three or more bytes that starts with one of them (the way a test writes a frame
  as ``"330111"``, ``"aa 01 00"`` or ``bytes.fromhex("aa0100")``).

Its scope: the whole of tests/firmware_sim.py and tests/test_h5083_firmware.py, the FirmwareGovee
class, and, in every other test module, each top-level definition or statement that has the SKU
``"H5083"`` as a string of its own (the SKU a device is made for, a parametrize row, an identify
result). A name such as ``"ihoment_H5083_A2D1"`` alone doesn't bring a test into scope: such tests
are about names, and use no H5083 frame.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from govee_ble_local.wire import _spec

TESTS = Path(__file__).resolve().parent
SKU = "H5083"
WHOLE_FILES = ("firmware_sim.py", "test_h5083_firmware.py")
DEFINITIONS = {"fake_ble.py": {"FirmwareGovee"}}
HELPERS = {"frame", "_frame", "status_burst"}
# Every frame's first byte is its pro_type; bleak hands a manufacturer value over starting with the
# Govee marker's second byte (govee_manufacturer_data.is_govee: company id 0x88 0xEC).
GOVEE_MARKER = 0xEC
HEADS = frozenset({int(p) for p in _spec.Common.ProType} | {GOVEE_MARKER})
_HEX = re.compile(r"(?i)\s*([0-9a-f]{2})(?:\s?[0-9a-f]{2}){2,}\s*")


def _int(node: ast.AST) -> int | None:
    return node.value if isinstance(node, ast.Constant) and type(node.value) is int else None


def hand_frames(tree: ast.AST) -> list[tuple[int, str]]:
    """Each frame-shaped literal under ``tree``, as (line, what)."""
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        line = getattr(node, "lineno", 0)
        if isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Name) and fn.id in HELPERS:
                found.append((line, f"{fn.id}(...)"))
            elif (isinstance(fn, ast.Name) and fn.id in ("bytes", "bytearray") and len(node.args) == 1
                  and isinstance(node.args[0], (ast.List, ast.Tuple)) and len(node.args[0].elts) >= 2
                  and _int(node.args[0].elts[0]) in HEADS):
                found.append((line, f"{fn.id}([0x{_int(node.args[0].elts[0]):02x}, ...])"))
        elif isinstance(node, ast.Constant) and isinstance(node.value, bytes):
            if len(node.value) >= 2 and node.value[0] in HEADS:
                found.append((line, f"b'\\x{node.value[0]:02x}...'"))
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            m = _HEX.fullmatch(node.value)
            if m and int(m.group(1), 16) in HEADS:
                found.append((line, repr(node.value)))
    return found


def _names_sku(node: ast.AST) -> bool:
    return any(isinstance(n, ast.Constant) and n.value == SKU for n in ast.walk(node))


def _definition(node: ast.stmt) -> str | None:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return node.name
    return None


def scoped(path: Path) -> list[ast.AST]:
    """The parts of a test module the lint holds to the rule."""
    tree = ast.parse(path.read_text(), str(path))
    if path.name in WHOLE_FILES:
        return [tree]
    named = DEFINITIONS.get(path.name, set())
    return [s for s in tree.body if _definition(s) in named or _names_sku(s)]


def _modules() -> list[Path]:
    return sorted(p for p in TESTS.glob("*.py") if p.name != Path(__file__).name)


@pytest.mark.parametrize("path", _modules(), ids=lambda p: p.name)
def test_no_hand_built_frames_where_the_firmware_image_answers(path: Path) -> None:
    found = [f"{path.name}:{line}: {what}" for part in scoped(path) for line, what in hand_frames(part)]
    assert found == [], "build these through the spec (fake_ble.FirmwareGovee) or the library:\n" + \
        "\n".join(found)


def test_the_scope_covers_the_simulator_and_every_h5083_module() -> None:
    for name in (*WHOLE_FILES, *DEFINITIONS):
        assert (TESTS / name).is_file(), name
    covered = {p.name for p in _modules() if scoped(p)}
    assert set(WHOLE_FILES) | set(DEFINITIONS) <= covered
    assert {"test_device_races.py", "test_fix_wp3_device_bugs.py", "test_identify.py"} <= covered


@pytest.mark.parametrize("source", [
    "frame(0xAA, 0x01, 0x01)",
    "_frame(0x33, 0x01, 0x00)",
    "status_burst(tlvs)",
    "bytes.fromhex('aa0100')",
    "b'\\x33\\x01\\x11'",
    "bytes([0xAA, 0x01, 1])",
    "bytearray([0xE7, 0x01])",
    "{0x8843: bytes([0xEC, 0, 2, 2, 1])}",
    "'330111'",
    "'aa 07 02 d0'",
])
def test_the_lint_finds_a_hand_built_frame(source: str) -> None:
    assert hand_frames(ast.parse(source)), source


@pytest.mark.parametrize("source", [
    "build.switch(True, relay=True)",
    "bytes(8)",
    "b'12345678'",
    "[f[:2].hex() for f in sent] == ['33b2', '3301']",
    "frame_[1] == build.CMD_SECRET_WRITE",
    "bytes([state.relay0])",
    "'ihoment_H5083_A2D1'",
    "'AA:BB:CC:DD:EE:83'",
    "bytes.fromhex('615521090b735c54')",
])
def test_the_lint_passes_library_and_spec_built_frames(source: str) -> None:
    assert hand_frames(ast.parse(source)) == [], source
