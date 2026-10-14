"""Characterization tests for Device paths not covered elsewhere (audit TEST-2, device part).

Tests tagged ``# CURRENT BEHAVIOR (BUG-n / DES-n)`` pin what the code does TODAY, including
behavior that is known to be wrong or questionable; they must be updated when the bug is fixed.
Offline and deterministic: the connection is mocked with AsyncMock, as in test_device_v3.py.
"""
from __future__ import annotations

import asyncio
import dataclasses
import logging
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fake_ble import ble_device

from govee_ble_local.devices.device import Device, make_device
from govee_ble_local.exceptions import GoveeBleError, GoveeBleInvalidValue, GoveeBleNotSupported
from govee_ble_local.models import Capability, Encryption


def _frame(*payload: int) -> bytes:
    b = bytearray(20)
    b[: len(payload)] = bytes(payload)
    x = 0
    for c in b[:19]:
        x ^= c
    b[19] = x
    return bytes(b)


def _dev(sku: str, replies: dict[int, list[bytes]] | None = None, **profile_overrides: Any) -> Device:
    """Device with a mocked connection. ``replies`` maps frame[1] (command byte) -> reply frames
    returned from query(); unmapped commands get no reply."""
    d = make_device(ble_device("AA:BB:CC:DD:EE:30", sku), sku)
    assert d is not None
    if profile_overrides:
        d.profile = dataclasses.replace(d.profile, **profile_overrides)
    sent: list[bytes] = []
    queried: list[bytes] = []
    table = replies or {}

    async def send(frame: bytes, **_k: object) -> None:
        sent.append(frame)

    async def send_many(frames: list[bytes]) -> None:
        sent.extend(frames)

    async def query(frame: bytes, **_k: object) -> list[bytes]:
        queried.append(frame)
        return list(table.get(frame[1], []))

    d._conn.send = AsyncMock(side_effect=send)  # type: ignore[method-assign]
    d._conn.send_many = AsyncMock(side_effect=send_many)  # type: ignore[method-assign]
    d._conn.send_unacked = AsyncMock(side_effect=send_many)  # type: ignore[method-assign]
    d._conn.query = AsyncMock(side_effect=query)  # type: ignore[method-assign]
    d._sent = sent  # type: ignore[attr-defined]
    d._queried = queried  # type: ignore[attr-defined]
    return d


def _sent(d: Device) -> list[bytes]:
    return list(d._sent)  # type: ignore[attr-defined]


def _queried(d: Device) -> list[bytes]:
    return list(d._queried)  # type: ignore[attr-defined]


def _adv(name: str | None, mfg: dict[int, bytes]) -> Any:
    return type("Adv", (), {"manufacturer_data": mfg, "local_name": name})()


# ── advertisement-driven encryption ─────────────────────────────────────────
def test_encryption_follows_encrypted_advertisement() -> None:
    d = _dev("H6008")  # profile default: NONE
    adv = _adv("Govee_H6008_ABCD", {0x8841: bytes([0xEC, 0x00, 0x01, 0x02])})  # 0x40 = encrypt bit
    assert d._resolve_encryption(adv) is Encryption.AES_RC4_PSK


def test_encryption_follows_plaintext_advertisement() -> None:
    d = _dev("H60A6")  # profile default: AES
    adv = _adv("Govee_H60A6_ABCD", {0x8801: bytes([0xEC, 0x00, 0x01, 0x02])})
    assert d._resolve_encryption(adv) is Encryption.NONE


def test_encryption_falls_back_to_profile_without_usable_advert() -> None:
    d = _dev("H60A6")
    assert d._resolve_encryption(None) is Encryption.AES_RC4_PSK
    # manufacturer data present but the name is not a Govee name -> identify() is None
    adv = _adv("SomethingElse", {0x8801: bytes([0xEC, 0, 1, 2])})
    assert d._resolve_encryption(adv) is Encryption.AES_RC4_PSK


def test_encryption_constructor_uses_advertisement() -> None:
    adv = _adv("Govee_H6008_ABCD", {0x8841: bytes([0xEC, 0x00, 0x01, 0x02])})
    d = Device(_dev("H6008").profile, ble_device("AA:BB:CC:DD:EE:31", "x"), adv)
    assert d._resolve_encryption(adv) is Encryption.AES_RC4_PSK


def test_encryption_unparseable_pact_keeps_profile_encryption_for_h60a6() -> None:
    # WAS (BUG-2): pact -1 (unparseable) made identify() report encrypted=False, resolving an
    # AES-profile H60A6 to NONE. FIXED (T6.1): pact -1 is "unknown", so the profile default wins.
    d = _dev("H60A6")
    adv = _adv("Govee_H60A6_ABCD", {0x1234: b"\x00\x00\x00\x00"})
    assert d._resolve_encryption(adv) is Encryption.AES_RC4_PSK
    assert d.profile.encryption is Encryption.AES_RC4_PSK


# ── power frames for a zoned fixture without a master switch ────────────────
def test_zoned_fixture_without_global_power_sends_one_frame_per_zone() -> None:
    d = _dev("H60A6", global_power=False)
    asyncio.run(d.set_power(True))
    assert [f[:4].hex() for f in _sent(d)] == ["33300001", "33300101"]
    assert d.state.zone_power == {0: True, 1: True}


# ── colour temperature / zones / gradual ────────────────────────────────────
def test_set_color_temp_updates_state_and_clears_rgb() -> None:
    d = _dev("H6008")
    asyncio.run(d.set_rgb((1, 2, 3)))
    asyncio.run(d.set_color_temp(4000))
    assert _sent(d)[-1][:3].hex() == "33050d"
    assert d.state.color_temp_kelvin == 4000 and d.state.rgb_color is None
    assert d.state.scene_code is None


def test_segment_color_temp_unsupported_scheme_is_not_supported() -> None:
    d = _dev("H6008")
    d.profile = dataclasses.replace(d.profile, capabilities=d.profile.capabilities | {Capability.SEGMENTS})
    with pytest.raises(GoveeBleNotSupported):
        asyncio.run(d.set_segment_color_temp([0], 4000))  # h6006 CCT has no segment mask
    assert _sent(d) == []


def test_set_zone_rgb_sends_zone_mask_and_updates_segments() -> None:
    d = _dev("H60A6")
    asyncio.run(d.set_zone_rgb("main", (10, 20, 30)))
    f = _sent(d)[0]
    assert f[:3].hex() == "330515" and f[12:14] == bytes([0x00, 0x10])  # mask 0x1000 (LE lo, hi)
    assert {s.index: s.rgb for s in d.state.segments} == {12: (10, 20, 30)}


def test_set_zone_rgb_zone_without_segments_raises() -> None:
    d = _dev("H60A6")
    zone = dataclasses.replace(d.profile.zones[0], segments=())
    d.profile = dataclasses.replace(d.profile, zones=(zone, d.profile.zones[1]))
    with pytest.raises(GoveeBleNotSupported):
        asyncio.run(d.set_zone_rgb("main", (1, 2, 3)))
    with pytest.raises(GoveeBleNotSupported):
        asyncio.run(d.set_zone_color_temp("main", 4000))
    assert _sent(d) == []


def test_set_zone_color_temp_sends_zone_mask() -> None:
    d = _dev("H60A6")
    asyncio.run(d.set_zone_color_temp("background", 4000))
    f = _sent(d)[0]
    assert f[:3].hex() == "330515"
    assert f[7:9] == (4000).to_bytes(2, "big") and f[12:14] == bytes([0xFF, 0x0F])


def test_set_zone_color_temp_unsupported_scheme_and_unknown_zone() -> None:
    d = _dev("H60A6", color_scheme="h6006")
    with pytest.raises(GoveeBleNotSupported):
        asyncio.run(d.set_zone_color_temp("main", 4000))
    with pytest.raises(GoveeBleNotSupported):
        asyncio.run(d.set_zone_color_temp("nope", 4000))
    with pytest.raises(GoveeBleNotSupported):
        asyncio.run(d.set_zone_power("nope", True))


def test_set_gradual_on_supported_and_unsupported() -> None:
    d = _dev("H61A8")
    asyncio.run(d.set_gradual(True))
    assert d.state.gradual is True and len(_sent(d)) == 1
    with pytest.raises(GoveeBleNotSupported):
        asyncio.run(_dev("H6008").set_gradual(True))


# ── update() read-back paths ────────────────────────────────────────────────
def test_update_plug_reads_relay_then_device_info() -> None:
    d = _dev("H5080", {0x01: [_frame(0xAA, 0x01, 0x01)]})
    st = asyncio.run(d.update())
    assert st is d.state and st.is_on is True and st.optimistic is False
    assert d._polled_once is True and d._device_info_read is True


def test_update_reads_gradual_flag() -> None:
    d = _dev("H61A8", {0xA3: [_frame(0xAA, 0xA3, 0x01)]})
    asyncio.run(d.update())
    assert d.state.gradual is True
    d2 = _dev("H61A8", {0xA3: [_frame(0xAA, 0xA3, 0x00)]})
    asyncio.run(d2.update())
    assert d2.state.gradual is False


def test_update_gradual_no_reply_leaves_state() -> None:
    d = _dev("H61A8")
    asyncio.run(d.update())
    assert d.state.gradual is None


def test_live_ic_read_failure_skips_segment_readback() -> None:
    d = _dev("H6641")  # color_readback_live_ic; no reply to the 0x40 IC-count query
    asyncio.run(d._read_mechanism_a_direct())
    assert d.state.segments == []
    assert all(f[1] == 0x40 for f in _queried(d))  # no per-group reads attempted


def test_device_info_serial_falls_back_to_sn_selector() -> None:
    sn = _frame(0xAA, 0x07, 0x02, 0x5C, 0xE7, 0x53, 0xF4, 0x74, 0x56, 0, 0)
    d = _dev("H60A6")
    # Only the SN (0x02) selector answers; basic (0x10) / wifi (0x11) carry nothing.

    async def query(frame: bytes, **_k: object) -> list[bytes]:
        return [sn] if frame[2] == 0x02 else []

    d._conn.query = AsyncMock(side_effect=query)  # type: ignore[method-assign]
    asyncio.run(d._read_device_info())
    assert d.state.serial_number == "56:74:F4:53:E7:5C"


def test_update_status_logs_debug_state_after_first_poll(caplog: pytest.LogCaptureFixture) -> None:
    d = _dev("H6008")
    d._polled_once = True
    with caplog.at_level(logging.DEBUG, logger="govee_ble_local.devices.device"):
        asyncio.run(d.update())
    assert any("state:" in r.message for r in caplog.records)


# ── characterization of known-buggy / questionable behavior ─────────────────
def test_bar_switch_zone_power_with_failed_read_refuses_to_guess_other_bar() -> None:
    # WAS (BUG-3): with no reply to the 0x36 read the unknown other bar was sent OFF.
    # FIXED (T6.1): raises GoveeBleError before sending anything.
    d = _dev("H6047")  # no reply to the 0x36 query
    with pytest.raises(GoveeBleError, match="unknown"):
        asyncio.run(d.set_zone_power("left", True))
    assert _queried(d)[0][1] == 0x36
    assert _sent(d) == [] and d.state.zone_power == {}


def test_raising_callback_does_not_break_set_power_or_later_callbacks() -> None:
    # WAS (DES-2): a raising callback propagated out of set_power and skipped later callbacks.
    # FIXED (T6.1): each callback is isolated (logged at WARNING); later ones still run.
    d = _dev("H6008")
    later: list[object] = []

    def boom(_s: object) -> None:
        raise RuntimeError("callback failed")

    d.register_callback(boom)
    d.register_callback(later.append)
    asyncio.run(d.set_power(True))
    assert len(_sent(d)) == 1 and d.state.is_on is True
    assert len(later) == 1


def test_invalid_inputs_are_not_validated_consistently() -> None:
    # WAS (DES-1): out-of-range RGB escaped as a raw ValueError. FIXED (T5.2/T6.1): it is a
    # GoveeBleInvalidValue (still a ValueError); nothing is sent and state is untouched.
    d = _dev("H6008")
    with pytest.raises(GoveeBleInvalidValue) as ei:
        asyncio.run(d.set_rgb((300, 0, 0)))
    assert not isinstance(ei.value, GoveeBleNotSupported)
    assert _sent(d) == [] and d.state.rgb_color is None

    # CURRENT BEHAVIOR (DES-1): a ValueError from the frame builder is reported as
    # GoveeBleNotSupported (here H61A8 has no per-segment brightness).
    h61 = _dev("H61A8")
    with pytest.raises(GoveeBleNotSupported):
        asyncio.run(h61.set_segment_brightness([0], 50))

    # kelvin beyond u16 once wrapped silently (70000 -> 0x1170 on the wire, unwrapped value in
    # state). Like the app (KelvinConfig.calibrationKelvin) it is now clamped to the profile
    # range, so what is sent and stored is the in-range value; a non-int is still rejected.
    t = _dev("H6008")
    asyncio.run(t.set_color_temp(70000))
    assert _sent(t)[0][6:8].hex() == "1964" and t.state.color_temp_kelvin == 6500
    with pytest.raises(GoveeBleInvalidValue):
        asyncio.run(t.set_color_temp(70000.0))  # type: ignore[arg-type]


def test_device_info_flag_unset_when_read_raises_and_retried() -> None:
    # WAS (BUG-5): _device_info_read was set True before any read, so a failed read was never
    # retried. FIXED (T6.1): it is set only after the reads complete without raising, so a
    # raising read leaves it False and the next poll retries.
    d = _dev("H6008")

    async def query(frame: bytes, **_k: object) -> list[bytes]:
        if frame[1] == 0x07:
            raise TimeoutError("device-info read failed")
        return []

    d._conn.query = AsyncMock(side_effect=query)  # type: ignore[method-assign]
    assert d._device_info_read is False
    with pytest.raises(TimeoutError):
        asyncio.run(d.update())
    assert d._device_info_read is False
    d._conn.query.reset_mock()  # type: ignore[attr-defined]
    with pytest.raises(TimeoutError):
        asyncio.run(d.update())  # next poll re-attempts the 0x07 reads
    assert any(c.args[0][1] == 0x07 for c in d._conn.query.call_args_list)  # type: ignore[attr-defined]


def test_update_propagates_read_failure_and_skips_notify() -> None:
    # CURRENT BEHAVIOR: a raising read aborts update(); state keeps its optimistic flag, no
    # callback fires and the first-poll log flag is not set.
    d = _dev("H6008")
    d._conn.query = AsyncMock(side_effect=TimeoutError("boom"))  # type: ignore[method-assign]
    seen: list[object] = []
    d.register_callback(seen.append)
    with pytest.raises(TimeoutError):
        asyncio.run(d.update())
    assert d.state.optimistic is True and seen == [] and d._polled_once is False


def test_update_unknown_readback_value_is_rejected_at_profile_construction() -> None:
    # WAS (DES-7): an unrecognised ``readback`` value was neither an error nor a warning;
    # update() skipped the state read, then still read device info and notified.
    # FIXED (T5.1): the profile now validates ``readback`` on construction, so a bogus value
    # raises ValueError before any Device exists.
    with pytest.raises(ValueError):
        _dev("H6008", readback="bogus")
