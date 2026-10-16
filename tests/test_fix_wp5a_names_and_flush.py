"""tools/field_test: a connect failure's text (bleak-retry-connector puts the advertised name in it) is
scrubbed of the name's MAC digits, and the capture is redacted even if flushing the frame log fails.
No BLE hardware."""
from __future__ import annotations

import asyncio
import importlib
import json
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest
from bleak.backends.device import BLEDevice
from bleak_retry_connector import BleakAbortedError, ble_device_description

from govee_ble_local.exceptions import GoveeBleConnectionError

TOOLS = Path(__file__).resolve().parent.parent / "tools"
ADDR = "98:17:3C:B1:A2:D1"
WIFI = "98:17:3C:B1:A2:D0"    # the Wi-Fi MAC the device derives: BLE MAC minus one
BLUEZ_PATH = f"/org/bluez/hci0/dev_{ADDR.replace(':', '_')}"


@pytest.fixture
def ft(monkeypatch: pytest.MonkeyPatch) -> Iterator[ModuleType]:
    monkeypatch.syspath_prepend(str(TOOLS))
    yield importlib.import_module("field_test")


@pytest.fixture
def runner(ft: ModuleType) -> ModuleType:
    return importlib.import_module("field_test.runner")


@pytest.fixture
def masking(ft: ModuleType) -> ModuleType:
    return importlib.import_module("field_test.masking")


def _identifying(text: str) -> list[str]:
    """Every spelling of ADDR / WIFI past the vendor prefix, and the name's MAC digits, in ``text``."""
    forms = ["a2d1"]
    for mac in (ADDR.lower(), WIFI.lower()):
        tail = mac.split(":")[3:]
        forms += [*(sep.join(tail) for sep in (":", "-", "_")), mac.replace(":", "")]
    return [f for f in forms if f in text.lower()]


def _connect_error(name: str, address: str = ADDR) -> GoveeBleConnectionError:
    """A connect error that names the device, wrapped as ``GoveeBleConnectionError("<addr>: connect
    failed: <message>")`` (transport/connection.py). Defensive case: bleak-retry-connector 4.6.1 builds
    ``f"{name} - {description}: ..."`` with ``device.address`` as the description (and connection.py
    passes the address as ``name``), so it never names the device; other releases within the
    dependency floor may (``<address> - <name> -> <adapter>``), so the test builds that string itself."""
    ble = BLEDevice(address, name, {"path": BLUEZ_PATH})
    inner = BleakAbortedError(f"{address} - {ble_device_description(ble)}: Failed to connect after "
                              "4 attempt(s): le-connection-abort-by-local")
    return GoveeBleConnectionError(f"{address}: connect failed: {inner}")


# --- the scrubber -----------------------------------------------------------------------------


@pytest.mark.parametrize(("name", "masked"), [
    ("ihoment_H6188_A2D1", "ihoment_H6188_xxxx"),
    ("Govee_H6008_A2D1", "Govee_H6008_xxxx"),
    ("Minger_H6001_A2D1", "Minger_H6001_xxxx"),
    ("GBK_H6188_A2D1", "GBK_H6188_xxxx"),
    ("GVH6188_A2D1", "GVH6188_xxxx"),
    ("GVH60A6A2D1", "GVH60A6xxxx"),
    ("GV6188A2D1", "GV6188xxxx"),
])
def test_mask_text_masks_the_mac_digits_of_any_govee_name(
    masking: ModuleType, name: str, masked: str
) -> None:
    text = str(_connect_error(name))
    assert name in text   # the defensive message shape carries the advertised name
    out = masking.mask_text(text, ADDR)
    assert _identifying(out) == [], out
    assert f" - {masked} -> /org/bluez/hci0: Failed to connect after 4 attempt(s)" in out
    assert masking.mask_text(out, ADDR) == out   # idempotent
    # The name shapes are recognised without the address too (a macOS UUID says nothing of the MAC).
    uuid = "A1B2C3D4-1111-2222-3333-444455556666"
    assert masking.mask_text(f"{uuid} - {name}: Failed", uuid) == f"A1B2C3D4-… - {masked}: Failed"


def test_mask_text_masks_the_given_names_whatever_their_shape(masking: ModuleType) -> None:
    text = f"{ADDR} - Light_A2D1: Failed to connect"
    assert "A2D1" in masking.mask_text(text, ADDR)   # not a Govee name shape: only known by name
    assert masking.mask_text(text, ADDR, ("Light_A2D1", None)) == (
        "98:17:3C:xx:xx:xx - Light_xxxx: Failed to connect")
    err = GoveeBleConnectionError(text)
    assert masking.describe_error(err, ADDR, ["Light_A2D1"]) == (
        "GoveeBleConnectionError: 98:17:3C:xx:xx:xx - Light_xxxx: Failed to connect")


def test_mask_text_leaves_step_names_alone_even_when_the_mac_ends_in_their_digits(
    masking: ModuleType,
) -> None:
    keep = '"step": "color_temp_2700", "segment_mid_brightness_20", rgb_pick_equal_to_4000k_tint'
    assert masking.mask_text(keep, "98:17:3C:B1:27:00", ("ihoment_H6188_2700",)) == keep


# --- the report -------------------------------------------------------------------------------


class _Device:
    encryption = "aes_rc4_psk"
    scene_names: list[str] = []   # noqa: RUF012 - test double

    def __init__(self, error: Exception, connect_fails: bool) -> None:
        self.error = error
        self.connect_fails = connect_fails

    async def connect(self) -> None:
        if self.connect_fails:
            raise self.error

    async def stop(self) -> None: ...

    async def update(self) -> SimpleNamespace:
        return SimpleNamespace(is_on=True)

    async def set_power(self, _on: bool) -> None:   # a mid-run reconnect that fails
        raise self.error


def _run(ft: Any, runner: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, dev: Any,
         name: str, sku: str) -> tuple[int, str]:
    plan = ft.SkuPlan(sku=sku, title="t", snapshot_keys=("is_on",), build_steps=lambda ctx: [
        ft.Step("off", lambda: ctx.dev.set_power(False), None, "Off?")])
    ft.register_plan(plan)
    ble = SimpleNamespace(address=ADDR, name=name)
    adv = SimpleNamespace(local_name=name, manufacturer_data={}, rssi=-50)

    async def fake_discover(_a: Any, _s: Any) -> tuple[Any, Any, list[Any], str]:
        return ble, adv, [], sku

    monkeypatch.setattr(runner, "discover", fake_discover)
    monkeypatch.setattr("govee_ble_local.create_device", lambda *_a, **_k: dev)
    try:
        rc = asyncio.run(runner.run(runner.parse_args(["--no-prompt", "--settle", "0"]), tmp_path, "T"))
    finally:
        ft.PLANS.pop(sku, None)
    return rc, (tmp_path / f"{sku.lower()}-report-T.json").read_text()


@pytest.mark.parametrize(("name", "masked"), [
    ("ihoment_H9995_A2D1", "ihoment_H9995_xxxx"),
    ("GV9995A2D1", "GV9995xxxx"),
    ("Light_A2D1", "Light_xxxx"),   # caught only because the runner hands the scrubber the name
])
def test_a_failed_connect_with_the_name_in_its_text_is_scrubbed(
    ft: Any, runner: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, name: str, masked: str
) -> None:
    dev = _Device(_connect_error(name), connect_fails=True)
    rc, text = _run(ft, runner, monkeypatch, tmp_path, dev, name, "H9992")
    rep = json.loads(text)
    assert rc == 1 and rep["result"] == (
        "aborted: GoveeBleConnectionError: 98:17:3C:xx:xx:xx: connect failed: 98:17:3C:xx:xx:xx - "
        f"98:17:3C:xx:xx:xx - {masked} -> /org/bluez/hci0: Failed to connect after 4 attempt(s): "
        "le-connection-abort-by-local")
    assert _identifying(text) == [], text
    assert rep["device"]["name"] == masked


@pytest.mark.parametrize("name", ["ihoment_H9995_A2D1", "Light_A2D1"])
def test_a_failed_reconnect_mid_run_is_scrubbed(
    ft: Any, runner: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, name: str
) -> None:
    dev = _Device(_connect_error(name), connect_fails=False)
    rc, text = _run(ft, runner, monkeypatch, tmp_path, dev, name, "H9991")
    rep = json.loads(text)
    assert rc == 0 and rep["steps"][0]["sent"].startswith(
        "error: GoveeBleConnectionError: 98:17:3C:xx:xx:xx: connect failed:")
    assert _identifying(text) == [], text


# --- the frame-log flush ----------------------------------------------------------------------


def _capture(path: Path) -> None:
    rows = [{"ts": 1, "addr": ADDR, "dir": "tx", "plain": None, "wire": "e701" + "ab" * 18, "enc": "e7"},
            {"ts": 2, "addr": ADDR, "dir": "rx", "plain": "aa07" + "11" * 18, "wire": "cd" * 20}]
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


@pytest.mark.parametrize("breakage", ["flush-signature", "private-names-gone"])
def test_the_capture_is_redacted_even_if_flushing_the_frame_log_fails(
    ft: Any, runner: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, breakage: str
) -> None:
    """A change to the library's frame-log internals (or a public flush with another signature)
    must fall back to redacting straight away, never leave the raw capture behind."""
    from govee_ble_local.transport import framelog

    if breakage == "flush-signature":
        monkeypatch.setattr(framelog, "flush", lambda: True, raising=False)   # flush(timeout): TypeError
    else:
        monkeypatch.delattr(framelog, "_QUEUE")
    frames = tmp_path / "h9990-frames-T.jsonl"

    class _Logged(_Device):
        async def stop(self) -> None:
            _capture(frames)   # the capture as the library left it

    dev = _Logged(GoveeBleConnectionError("unused"), connect_fails=False)

    async def on(_on: bool) -> None: ...

    monkeypatch.setattr(dev, "set_power", on)
    rc, text = _run(ft, runner, monkeypatch, tmp_path, dev, "ihoment_H9990_A2D1", "H9990")
    rep = json.loads(text)
    assert rc == 0 and rep["result"] == "completed" and rep["frames_redacted"] == 2
    raw = frames.read_text().lower()
    assert "abab" not in raw and "cdcd" not in raw and "1111" not in raw and _identifying(raw) == []
