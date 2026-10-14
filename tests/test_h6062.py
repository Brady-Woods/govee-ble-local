"""H6062 (Glide wall light, striplight split, goodsType 41): the h60a6 0x15 layout with the tint,
the `aa 41` section-count read, masks that follow the device-reported segment count, the
`aa a5` read-back walk of ceil(sectionNum / 4) groups, and no scenes (no catalog is bundled yet).

All expectations are derived from the decompiled Govee Home 7.7.02 app (see the H6062 entry in
spec/devices.yaml for citations); none are hardware-verified. Golden frames are written out as hex
(not built with the library) so a builder regression cannot hide behind itself."""
from __future__ import annotations

import asyncio
import importlib
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fake_ble import ble_device

from govee_ble_local import Capability, Encryption
from govee_ble_local.devices.device import Device, make_device
from govee_ble_local.devices.profile import profile_for
from govee_ble_local.exceptions import GoveeBleError, GoveeBleInvalidValue, GoveeBleNotSupported
from govee_ble_local.wire import build as b
from govee_ble_local.wire import parse


def _frame(*payload: int) -> bytes:
    buf = bytearray(20)
    buf[: len(payload)] = bytes(payload)
    x = 0
    for c in buf[:19]:
        x ^= c
    buf[19] = x
    return bytes(buf)


def _hex(prefix: str) -> bytes:
    """A 20-byte frame from a hex payload (zero padded, XOR checksum)."""
    return _frame(*bytes.fromhex(prefix))


def _c(rgb: str, mask: str) -> bytes:
    """Golden 0x15 colour frame: 33 05 15 01 <rgb> 00 00 00 00 00 <mask>."""
    return _hex("33051501" + rgb + "0000000000" + mask)


def _section(ic: int, section: int) -> bytes:
    return _frame(0xAA, 0x41, ic >> 8, ic & 0xFF, section)


def _group(index: int, *records: tuple[int, int, int, int]) -> bytes:
    return _frame(0xAA, 0xA5, index, *(v for r in records for v in r))


class _Dev:
    """A Device on a mocked connection that logs every query and write, in order."""

    def __init__(self, replies: dict[int, list[bytes]] | None = None) -> None:
        d = make_device(ble_device("AA:BB:CC:DD:EE:62", "H6062"), "H6062")
        assert d is not None
        self.dev: Device = d
        self.log: list[tuple[str, bytes]] = []
        self.replies = replies or {}

        async def send(frame: bytes, **_k: object) -> None:
            self.log.append(("send", frame))

        async def send_many(frames: list[bytes]) -> None:
            self.log.extend(("send", f) for f in frames)

        async def query(frame: bytes, **_k: object) -> list[bytes]:
            self.log.append(("query", frame))
            if frame[1] == 0xA5:
                return list(self.replies.get(0xA5 << 8 | frame[2], []))
            return list(self.replies.get(frame[1], []))

        d._conn.send = AsyncMock(side_effect=send)  # type: ignore[method-assign]
        d._conn.send_many = AsyncMock(side_effect=send_many)  # type: ignore[method-assign]
        d._conn.send_unacked = AsyncMock(side_effect=send_many)  # type: ignore[method-assign]
        d._conn.query = AsyncMock(side_effect=query)  # type: ignore[method-assign]

    @property
    def sent(self) -> list[bytes]:
        return [f for k, f in self.log if k == "send"]

    @property
    def queried(self) -> list[bytes]:
        return [f for k, f in self.log if k == "query"]


def _with_count(section: int) -> _Dev:
    return _Dev({0x41: [_section(0x0038, section)]})


# ── profile ──────────────────────────────────────────────────────────────────
def test_profile() -> None:
    p = profile_for("h6062")
    assert p is not None
    assert p.capabilities == {Capability.POWER, Capability.BRIGHTNESS, Capability.RGB,
                              Capability.COLOR_TEMP, Capability.SEGMENTS}   # no SCENES: no catalog
    assert p.color_scheme == "h60a6" and p.zones == () and not p.global_power
    assert (p.min_kelvin, p.max_kelvin) == (2000, 9000)        # KelvinConfig version 0
    assert p.brightness_range is None and p.brightness_max == 100   # percent, 1..100
    assert p.encryption is Encryption.NONE and not p.requires_secret
    assert p.readback == "polled" and p.section_count_read
    assert p.color_readback == "mechanism_a_direct" and p.color_readback_per_batch == 4
    assert p.color_readback_segments is None and not p.color_readback_live_ic
    assert p.segments == 33                                     # upper bound: pact (4,1)


def test_only_h6062_reads_the_section_count() -> None:
    from govee_ble_local.devices.profile import PROFILES
    assert [p.skus for p in PROFILES if p.section_count_read] == [("H6062",)]


# ── aa 41 frame + parser ─────────────────────────────────────────────────────
def test_section_count_query_frame() -> None:
    f = b.section_count_query()
    assert f.hex() == "aa41" + "00" * 17 + "eb"      # 0xAA ^ 0x41 = 0xEB; no payload (BleUtils.p)
    assert f != b.ic_count_query()                   # aa 40 is a different read


def test_parse_section_count() -> None:
    sc = parse.parse_section_count(_section(0x0138, 28))
    assert sc == parse.SectionCount(ic_count=0x0138, section_num=28)
    assert parse.parse_section_count(_section(0, 33)) == parse.SectionCount(0, 33)


def test_parse_section_count_rejects_other_frames() -> None:
    assert parse.parse_section_count(_frame(0xAA, 0x40, 0, 56, 14)) is None    # aa 40 reply
    assert parse.parse_section_count(_frame(0x33, 0x41, 0, 56, 14)) is None    # a write
    assert parse.parse_section_count(b"\xaa\x41\x00\x38") is None              # truncated


# ── frames: golden hex, per the three mask widths the app produces ───────────
#   n <= 16: 2 bytes; 17..24: 3 bytes; 25..32: 4 bytes; 33: 5 bytes (BleUtils.X).
@pytest.mark.parametrize(("n", "mask"), [
    (12, "ff0f"), (16, "ffff"), (19, "ffff07"), (24, "ffffff"), (28, "ffffff0f"), (33, "ffffffff01")])
def test_whole_device_rgb_golden(n: int, mask: str) -> None:
    t = _with_count(n)
    asyncio.run(t.dev.set_rgb((255, 0, 0)))
    assert t.sent == [_c("ff0000", mask)]


@pytest.mark.parametrize(("n", "mask"), [(19, "ffff07"), (28, "ffffff0f"), (33, "ffffffff01")])
def test_whole_device_cct_golden_carries_the_tint(n: int, mask: str) -> None:
    # 33 05 15 01 ff ff ff <K u2be> <tint r g b> <mask>   (Bytes.r; tint = getTemColorByKelvin)
    for kelvin, tail in ((2000, "07d0ff8d0b"), (4000, "0fa0ffd5a1"), (6500, "1964fff9fb"),
                         (9000, "2328d9e1ff")):
        t = _with_count(n)
        asyncio.run(t.dev.set_color_temp(kelvin))
        assert t.sent == [_hex("33051501ffffff" + tail + mask)], kelvin


def test_segment_writes_golden_n19() -> None:
    t = _with_count(19)
    d = t.dev
    asyncio.run(d.set_segment_rgb([0], (1, 2, 3)))
    asyncio.run(d.set_segment_rgb([9, 10], (0, 255, 0)))
    asyncio.run(d.set_segment_rgb([18], (0, 0, 255)))
    asyncio.run(d.set_segment_brightness([18], 20))              # Bytes.G: 15 02 <pct> <mask>
    asyncio.run(d.set_segment_color_temp([9], 2700))
    assert t.sent == [
        _c("010203", "010000"),
        _c("00ff00", "000600"),
        _c("0000ff", "000004"),
        _hex("3305150214" + "000004"),
        _hex("33051501ffffff0a8cffae54" + "000200"),
    ]


def test_segment_writes_golden_n33_five_byte_mask() -> None:
    t = _with_count(33)
    asyncio.run(t.dev.set_segment_rgb([32], (255, 255, 255)))
    asyncio.run(t.dev.set_segment_rgb([0, 7, 8, 31], (9, 9, 9)))
    assert t.sent == [_c("ffffff", "0000000001"), _c("090909", "8101008000")]


def test_brightness_is_percent_and_power_is_global() -> None:
    t = _with_count(19)
    asyncio.run(t.dev.set_brightness(1))
    asyncio.run(t.dev.set_brightness(100))
    asyncio.run(t.dev.set_power(True))
    asyncio.run(t.dev.set_power(False))
    assert [f[:3].hex() for f in t.sent] == ["330401", "330464", "330101", "330100"]


def test_kelvin_range_and_100k_rounding() -> None:
    t = _with_count(19)
    for bad in (1999, 9001, 1500):
        with pytest.raises(GoveeBleInvalidValue):
            asyncio.run(t.dev.set_color_temp(bad))
    asyncio.run(t.dev.set_color_temp(2049))     # the app only sends the 100 K steps of its table
    asyncio.run(t.dev.set_color_temp(2050))
    assert [f[7:9].hex() for f in t.sent] == ["07d0", "0834"]
    assert t.dev.state.color_temp_kelvin == 2100


def test_turn_on_batches_and_reads_the_count_first() -> None:
    t = _with_count(19)
    asyncio.run(t.dev.turn_on(brightness=50, kelvin=2700))
    assert [k for k, _ in t.log][:2] == ["query", "send"]
    assert t.log[0][1] == b.section_count_query()
    assert [f[:3].hex() for f in t.sent] == ["330101", "330432", "330515"]
    assert t.sent[2] == _hex("33051501ffffff0a8cffae54" + "ffff07")


# ── the aa 41 read is part of connect/refresh, and mandatory before a mask is built ──────
def test_first_write_reads_aa41_once_then_reuses_it() -> None:
    t = _with_count(19)
    asyncio.run(t.dev.set_rgb((1, 2, 3)))
    asyncio.run(t.dev.set_segment_rgb([4], (1, 2, 3)))
    assert t.queried == [b.section_count_query()]       # one read, before the first colour frame
    assert [k for k, _ in t.log] == ["query", "send", "send"]
    assert t.dev.segment_count == 19


def test_power_and_brightness_do_not_need_the_count() -> None:
    t = _with_count(19)
    asyncio.run(t.dev.set_power(True))
    asyncio.run(t.dev.set_brightness(10))
    assert t.queried == []


@pytest.mark.parametrize("reply", [[], [_section(0x0038, 0)], [_section(0x0038, 57)],
                                   [_frame(0xAA, 0x41)[:3]]])
def test_unanswered_or_unusable_count_sends_no_colour_frame(reply: list[bytes]) -> None:
    t = _Dev({0x41: reply})
    for call in (lambda: t.dev.set_rgb((1, 2, 3)), lambda: t.dev.set_color_temp(4000),
                 lambda: t.dev.set_segment_rgb([0], (1, 2, 3)),
                 lambda: t.dev.set_segment_brightness([0], 10),
                 lambda: t.dev.set_segment_color_temp([0], 4000),
                 lambda: t.dev.turn_on(rgb=(1, 2, 3))):
        with pytest.raises(GoveeBleError, match="aa 41"):
            asyncio.run(call())
    assert t.sent == []


def test_segment_indices_follow_the_reported_count() -> None:
    t = _with_count(19)
    asyncio.run(t.dev.set_segment_rgb([18], (1, 1, 1)))
    with pytest.raises(GoveeBleInvalidValue):
        asyncio.run(t.dev.set_segment_rgb([19], (1, 1, 1)))
    assert len(t.sent) == 1


def test_a_later_poll_adopts_a_changed_count() -> None:
    t = _with_count(19)
    asyncio.run(t.dev.set_rgb((1, 2, 3)))
    t.replies[0x41] = [_section(0x0038, 24)]
    asyncio.run(t.dev.update())
    assert t.dev.segment_count == 24
    asyncio.run(t.dev.set_rgb((1, 2, 3)))
    assert t.sent[-1] == _c("010203", "ffffff")           # three mask bytes, 24 bits now


# ── read-back: connect order and the aa a5 walk of ceil(n / 4) groups ────────
def _poll_replies(section: int) -> dict[int, list[bytes]]:
    groups = (section + 3) // 4
    r: dict[int, list[bytes]] = {
        0x01: [_frame(0xAA, 0x01, 0x01)],
        0x04: [_frame(0xAA, 0x04, 0x32)],
        0x41: [_section(0x0038, section)],
        0x05: [_frame(0xAA, 0x05, 0x15, 0x01, 0x0F, 0xA0)],
    }
    for g in range(1, groups + 1):
        n = min(4, section - (g - 1) * 4)
        r[0xA5 << 8 | g] = [_group(g, *[(100, g, i, 0) for i in range(n)])]
    return r


@pytest.mark.parametrize(("section", "groups"), [(12, 3), (19, 5), (24, 6), (28, 7), (33, 9)])
def test_update_reads_aa41_then_walks_ceil_n_over_4_groups(section: int, groups: int) -> None:
    t = _Dev(_poll_replies(section))
    st = asyncio.run(t.dev.update())
    q = t.queried
    # the app's order (ControllerStrategyConfig.O): aa 01, aa 04, aa 41, aa 05; then the colour walk
    assert [f[:3].hex() for f in q[:4]] == ["aa0101", "aa0401", "aa4100", "aa0501"]
    walk = [f for f in q if f[1] == 0xA5]
    assert [f[2] for f in walk] == list(range(1, groups + 1))
    assert t.dev.segment_count == section
    assert [s.index for s in st.segments] == list(range(section))      # no phantom segments
    assert st.segments[-1].rgb == (groups, (section - 1) % 4, 0) and st.segments[0].brightness == 100
    assert st.is_on is True and st.brightness == 50 and st.color_temp_kelvin == 4000


def test_update_without_an_answer_skips_the_walk_but_keeps_the_poll() -> None:
    r = _poll_replies(19)
    del r[0x41]
    t = _Dev(r)
    st = asyncio.run(t.dev.update())
    assert [f for f in t.queried if f[1] == 0xA5] == []        # no guessed group count
    assert st.is_on is True and st.brightness == 50 and st.segments == []


def test_no_scenes_offered() -> None:
    t = _with_count(19)
    assert t.dev.scene_names == []
    with pytest.raises(GoveeBleNotSupported):
        asyncio.run(t.dev.set_scene(0x0B20))
    assert t.sent == []


# ── field-test plan (offline, real Device on the mocked connection) ──────────
def test_field_test_plan_runs_end_to_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parent.parent / "tools"))
    ft = importlib.import_module("field_test")
    runner = importlib.import_module("field_test.runner")
    assert "H6062" in ft.PLANS and ft.PLANS["H6062"].title == "H6062 Glide wall light"

    t = _Dev(_poll_replies(19))
    d = t.dev
    d.connect = AsyncMock()  # type: ignore[method-assign]
    d.stop = AsyncMock()  # type: ignore[method-assign]
    ble = ble_device("AA:BB:CC:DD:EE:62", "Govee_H6062_1A2B")

    class _Adv:
        def __init__(self) -> None:
            self.local_name = "Govee_H6062_1A2B"
            self.manufacturer_data: dict[int, bytes] = {}
            self.rssi = -60

    async def fake_discover(_a: object, _s: object) -> tuple[Any, Any, list[dict[str, Any]], str]:
        return ble, _Adv(), [{"name": "Govee_H6062_1A2B", "identified_sku": "H6062"}], "H6062"

    monkeypatch.setattr(runner, "discover", fake_discover)
    monkeypatch.setattr("govee_ble_local.create_device", lambda *_a, **_k: d)
    assert asyncio.run(runner.run(runner.parse_args(["--no-prompt", "--settle", "0"]), tmp_path, "T")) == 0
    rep = json.loads((tmp_path / "h6062-report-T.json").read_text())
    assert rep["result"] == "completed" and rep["plan"]["sku"] == "H6062"
    names = [r["step"] for r in rep["steps"]]
    assert names[:3] == ["segment_count", "power_off", "power_on"]
    assert {"color_temp_2000", "color_temp_9000", "segment_first_red", "segment_middle_red",
            "segment_last_red", "segment_last_brightness_20"} <= set(names)
    assert all(r["sent"] == "ok" for r in rep["steps"])
    # the read-back of the very first step holds exactly the 19 segments the device reported
    assert rep["steps"][0]["readback_matches"] is True
    # whole-device colour used the 3-byte mask of a 19-segment device
    assert _c("ff0000", "ffff07") in t.sent
    assert "AA:BB:CC:DD:EE:62" not in (tmp_path / "h6062-report-T.json").read_text()
