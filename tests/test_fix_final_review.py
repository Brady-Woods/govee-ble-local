"""Final review fixes: the aa 05 mode report decoded as the app does for the 0x0d bulbs, H6188 and
H6052, a scan failure that still matches 1.3.0's handlers, and an unreadable scene catalog."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from bleak.exc import BleakError
from conftest import require_optional

require_optional("kaitaistruct", reason="Kaitai runtime not installed")

from govee_ble_local import scanner, scenes  # noqa: E402
from govee_ble_local.devices import _fold  # noqa: E402
from govee_ble_local.devices.device import Device, make_device  # noqa: E402
from govee_ble_local.devices.profile import profile_for  # noqa: E402
from govee_ble_local.exceptions import GoveeBleConnectionError, GoveeBleError  # noqa: E402
from govee_ble_local.models import DeviceState  # noqa: E402
from govee_ble_local.wire import parse  # noqa: E402

from fake_ble import ble_device, frame  # noqa: E402

# The live H6052 capture (written by the white-slot CCT form) and the reviewers' repro frames.
_LIVE_0D = frame(0xAA, 0x05, 0x0D, 0xFF, 0xFF, 0xFF, 0x0A, 0x8C)
_REPORT_15 = frame(0xAA, 0x05, 0x15, 0x01, 0x0A, 0x8C, 0x00, 0x00)
_REPORT_0B = frame(0xAA, 0x05, 0x0B, 0x10, 0x20, 0x30, 0x00, 0x00)
_REPORT_02 = frame(0xAA, 0x05, 0x02, 0x40, 0x50, 0x60, 0x00, 0x00)
_NEGATIVE_K = frame(0xAA, 0x05, 0x0D, 1, 2, 3, 0x80, 0x00, 7, 8, 9)   # getSignedShort(80 00) < 0
_MUSIC = frame(0xAA, 0x05, 0x13, 0x01, 0x02)

_STRATEGY_A_SKUS = ("H6006", "H6008", "H6004", "H6188")


def _fold_reply(sku: str, reply: bytes, **start: Any) -> DeviceState:
    """State after folding ``reply`` into a DeviceState that starts as ``start``."""
    profile = profile_for(sku)
    assert profile is not None
    state = DeviceState(**start)
    _fold.apply_mode_reply(profile, state, reply)
    return state


# ── H6052: H6052InfoDetail.G reads a 0x0d report as [r, g, b] only ─────────────────────────
def test_h6052_0d_report_is_rgb_and_its_kelvin_bytes_are_not_read() -> None:
    assert profile_for("H6052").mode_color_parse == "rgb_0d"  # type: ignore[union-attr]
    st = _fold_reply("H6052", _LIVE_0D, color_temp_kelvin=2700)
    assert (st.rgb_color, st.color_temp_kelvin) == ((255, 255, 255), None)
    assert parse.parse_mode_0d_rgb(_LIVE_0D) == (255, 255, 255)
    assert parse.parse_mode_0d_rgb(frame(0xAA, 0x05, 0x0D, 0xFF, 0x00, 0x00, 0x0F, 0xA0)) == (255, 0, 0)


@pytest.mark.parametrize("reply", [_REPORT_15, _REPORT_0B, _REPORT_02, _MUSIC])
def test_h6052_other_colour_sub_modes_change_nothing(reply: bytes) -> None:
    # G handles 0x0d, 0x04, 0x0a and 0x0f; 0x15 and the rest are "not support".
    for start in ({"rgb_color": (9, 9, 9)}, {"color_temp_kelvin": 3000}):
        st = _fold_reply("H6052", reply, **start)
        before = (start.get("rgb_color"), start.get("color_temp_kelvin"))
        assert (st.rgb_color, st.color_temp_kelvin) == before
    assert parse.parse_mode_0d_rgb(reply) is None


def test_h6052_rgb_parse_rejects_what_is_not_a_0d_report() -> None:
    assert parse.parse_mode_0d_rgb(b"\xaa\x05\x0d\x01\x02") is None   # body shorter than [r, g, b]
    assert parse.parse_mode_0d_rgb(frame(0xAA, 0x04, 0x0D, 1, 2, 3)) is None


# ── 0x0d bulbs and H6188: ModeParseStrategyConfig.a via OpInfo4Detail.parseModeValidBytes ─────
@pytest.mark.parametrize("sku", _STRATEGY_A_SKUS)
def test_strategy_a_reads_every_colour_sub_mode_with_the_0d_layout(sku: str) -> None:
    assert profile_for(sku).mode_color_parse == "rgb_kelvin_tint"  # type: ignore[union-attr]
    # 0x15: [01 0a 8c][00 00] -> kelvin 0, so the RGB (1, 10, 140), not CCT 2700 K
    st = _fold_reply(sku, _REPORT_15, color_temp_kelvin=3000)
    assert (st.rgb_color, st.color_temp_kelvin) == ((1, 10, 140), None)
    # 0x0b and 0x02 are decoded too, so a stale colour is replaced
    st = _fold_reply(sku, _REPORT_0B, rgb_color=(9, 9, 9))
    assert (st.rgb_color, st.color_temp_kelvin) == ((16, 32, 48), None)
    st = _fold_reply(sku, _REPORT_02, rgb_color=(9, 9, 9))
    assert (st.rgb_color, st.color_temp_kelvin) == ((64, 80, 96), None)
    # 0x0d with a Kelvin > 0 is CCT
    st = _fold_reply(sku, _LIVE_0D, rgb_color=(9, 9, 9))
    assert (st.rgb_color, st.color_temp_kelvin) == (None, 2700)
    # a negative signed Kelvin shows the tint bytes as a colour, not CCT (iG > 0 fails)
    st = _fold_reply(sku, _NEGATIVE_K, color_temp_kelvin=3000)
    assert (st.rgb_color, st.color_temp_kelvin) == ((7, 8, 9), None)
    # a sub-mode parseModeValidBytes sends elsewhere leaves the colour as it was
    st = _fold_reply(sku, _MUSIC, rgb_color=(9, 9, 9))
    assert (st.rgb_color, st.color_temp_kelvin) == ((9, 9, 9), None)


def test_strategy_a_parse_returns() -> None:
    assert parse.parse_mode_rgb_kelvin_tint(_REPORT_15) == ((1, 10, 140), None)
    assert parse.parse_mode_rgb_kelvin_tint(_LIVE_0D) == (None, 2700)
    top = frame(0xAA, 0x05, 0x0D, 0xFF, 0xAE, 0x54, 0x7F, 0xFF)      # the largest positive short
    assert parse.parse_mode_rgb_kelvin_tint(top) == (None, 0x7FFF)
    assert parse.parse_mode_rgb_kelvin_tint(_NEGATIVE_K) == ((7, 8, 9), None)
    assert parse.parse_mode_rgb_kelvin_tint(_MUSIC) is None
    assert parse.parse_mode_rgb_kelvin_tint(frame(0xAA, 0x05, 0x04, 0x09, 0x00)) is None   # scene
    assert parse.parse_mode_rgb_kelvin_tint(_LIVE_0D[:10]) is None   # kind 2 needs its 8 body bytes
    assert parse.parse_mode_rgb_kelvin_tint(frame(0xAA, 0x04, 0x0D, 1, 2, 3)) is None


def test_scene_report_still_wins_on_the_strategy_a_skus() -> None:
    st = _fold_reply("H6008", frame(0xAA, 0x05, 0x04, 0x09, 0x00), rgb_color=(9, 9, 9))
    assert (st.scene_code, st.rgb_color, st.color_temp_kelvin) == (9, None, None)


def test_h60a6_mode_parse_is_its_info4detail() -> None:
    # pact_h60a0 h60a1.Info4Detail.Q: 0x15 = [gradual][kelvin s16be], 0x0d = [r, g, b][kelvin s16be]
    # (CCT for a Kelvin > 0, else that RGB), nothing else. WAS the default "rgb_kelvin", whose 0x0d
    # decode took any non-zero Kelvin as CCT and read it unsigned.
    assert profile_for("H60A6").mode_color_parse == "kelvin_15_rgb_kelvin_0d"  # type: ignore[union-attr]
    st = _fold_reply("H60A6", _REPORT_15, rgb_color=(9, 9, 9))
    assert (st.rgb_color, st.color_temp_kelvin) == (None, 2700)
    st = _fold_reply("H60A6", _NEGATIVE_K, color_temp_kelvin=3000)
    assert (st.rgb_color, st.color_temp_kelvin) == ((1, 2, 3), None)
    for other in (_REPORT_0B, _REPORT_02, _MUSIC):   # "not support!": the colour is left as it was
        st = _fold_reply("H60A6", other, rgb_color=(9, 9, 9))
        assert (st.rgb_color, st.color_temp_kelvin) == ((9, 9, 9), None)


# ── WP1b-008 made compatible: a scan failure is also the error 1.3.0 raised ──────────────────
@pytest.mark.parametrize(("err", "family"), [
    (BleakError("Bluetooth adapter not found"), BleakError),
    (FileNotFoundError(2, "No such file or directory"), OSError),
    (ConnectionRefusedError(111, "Connection refused"), OSError),
    (EOFError(), EOFError),
])
async def test_scan_failure_matches_both_the_library_and_the_1_3_0_handler(
    monkeypatch: pytest.MonkeyPatch, err: Exception, family: type[Exception],
) -> None:
    async def failing_discover(**_kw: object) -> dict[str, object]:
        raise err

    monkeypatch.setattr(scanner.BleakScanner, "discover", failing_discover)
    with pytest.raises(family, match="BLE scan failed") as ei:   # a handler written for 1.3.0
        await scanner.discover(timeout=0.1)
    assert isinstance(ei.value, GoveeBleConnectionError) and isinstance(ei.value, GoveeBleError)
    assert ei.value.__cause__ is err


async def test_except_bleak_error_still_catches_a_failed_scan(monkeypatch: pytest.MonkeyPatch) -> None:
    async def failing_discover(**_kw: object) -> dict[str, object]:
        raise BleakError("Bluetooth adapter not found")

    monkeypatch.setattr(scanner.BleakScanner, "discover", failing_discover)
    caught: BaseException | None = None
    try:
        await scanner.discover(timeout=0.1)
    except BleakError as exc:   # the 1.3.0 caller's handler
        caught = exc
    assert isinstance(caught, BleakError) and isinstance(caught, GoveeBleConnectionError)


# ── an unreadable scene catalog is a library error ─────────────────────────────────────────
class _Unreadable:
    """Stands in for ``resources.files(...)``: every catalog file raises ``error`` when read."""

    def __init__(self, error: Exception) -> None:
        self.error = error

    def __truediv__(self, _name: str) -> _Unreadable:
        return self

    def read_text(self, encoding: str = "utf-8") -> str:
        raise self.error


@pytest.fixture
def unreadable(monkeypatch: pytest.MonkeyPatch) -> Any:
    def install(error: Exception) -> None:
        scenes._catalog.cache_clear()
        monkeypatch.setattr(scenes, "resources", SimpleNamespace(files=lambda _pkg: _Unreadable(error)))

    yield install
    scenes._catalog.cache_clear()   # nothing read through the stub stays cached


@pytest.mark.parametrize("error", [
    PermissionError(13, "Permission denied"),
    IsADirectoryError(21, "Is a directory"),
    UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte"),
])
def test_unreadable_catalog_raises_a_library_error(unreadable: Any, error: Exception) -> None:
    unreadable(error)
    with pytest.raises(GoveeBleError, match="could not be read") as ei:
        scenes.load_scenes("H6008")
    assert ei.value.__cause__ is error


def test_missing_catalog_is_still_empty(unreadable: Any) -> None:
    unreadable(FileNotFoundError(2, "No such file"))
    assert scenes.load_scenes("H6008") == {}


async def test_update_survives_an_unreadable_catalog(unreadable: Any) -> None:
    unreadable(PermissionError(13, "Permission denied"))
    d = make_device(ble_device("AA:BB:CC:DD:EE:5A", "H6008"), "H6008")
    assert isinstance(d, Device)
    d._conn.query = AsyncMock(return_value=[])  # type: ignore[method-assign]
    state = await d.update()   # the catalog warm-up's GoveeBleError is logged, not raised
    assert state is d.state
