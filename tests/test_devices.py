"""Device-level behavior tests (H6047 left/right bars)."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from bleak.backends.device import BLEDevice

from govee_ble_local.registry import create_device


def _h6047() -> object:
    dev = create_device(BLEDevice(address="AA:BB:CC:DD:EE:01", name="H6047", details={}), "H6047")
    dev._connection.send = AsyncMock()  # type: ignore[attr-defined]
    return dev


def test_h6047_has_left_right_zones() -> None:
    dev = _h6047()
    assert [z.name for z in dev.zones] == ["left", "right"]  # type: ignore[attr-defined]


def test_h6047_set_zone_power_sends_combined_frame() -> None:
    """Each bar toggle re-sends BOTH bar states in one 33 36 <left> <right>
    frame; state is tracked optimistically. Bars default to on when unknown."""
    dev = _h6047()

    async def go() -> None:
        await dev.set_zone_power("left", False)   # right still on -> 33 36 00 01
        await dev.set_zone_power("right", False)  # both off        -> 33 36 00 00
        await dev.set_zone_power("left", True)    # left back on    -> 33 36 01 00

    asyncio.run(go())

    sent = [c.args[0][:4].hex() for c in dev._connection.send.call_args_list]  # type: ignore[attr-defined]
    assert sent == ["33360001", "33360000", "33360100"]
    assert dev.zone_is_on("left") is True    # type: ignore[attr-defined]
    assert dev.zone_is_on("right") is False  # type: ignore[attr-defined]


def test_h6047_failed_send_does_not_commit_state() -> None:
    """A send failure must not update optimistic state (HA reverts)."""
    dev = _h6047()
    dev._connection.send = AsyncMock(side_effect=RuntimeError("no ack"))  # type: ignore[attr-defined]

    async def go() -> None:
        try:
            await dev.set_zone_power("left", False)
        except RuntimeError:
            pass

    asyncio.run(go())
    assert dev.zone_is_on("left") is None  # unchanged (unknown)  # type: ignore[attr-defined]


def test_h6641_registered_and_h60a6_scheme() -> None:
    from govee_ble_local.ble import controllers
    from govee_ble_local.identify import sku_from_local_name
    from govee_ble_local.registry import supported_skus

    assert "H6641" in supported_skus()
    assert sku_from_local_name("GVH66411A2B") == "H6641"
    assert sku_from_local_name("Govee_H6641_1A2B") == "H6641"

    dev = create_device(BLEDevice("AA:BB:CC:DD:EE:02", "GVH6641", details={}), "H6641")
    assert type(dev).__name__ == "GoveeLightH6641"
    assert (dev.min_kelvin, dev.max_kelvin) == (2000, 9000)
    # h60a6 colour scheme: 33 05 15 01 <rgb> ... <mask> (all-segments mask ff ff)
    assert controllers.rgb(255, 0, 0, dev._color_scheme, dev._segments)[:4].hex() == "33051501"


def test_scene_stub_bare_activated_real_uploaded(monkeypatch: object) -> None:
    """0xff-stub scene params must be bare-activated (33 05 04 <code>), not
    uploaded (verified against the app's BLE capture); real blobs still upload."""
    import base64
    from unittest.mock import AsyncMock as _AsyncMock

    from govee_ble_local.devices import base as basemod
    from govee_ble_local.scenes import Scene

    stub = base64.b64encode(bytes([0x50, 0x20, 0x00, 0xFF] + [0] * 40)).decode()
    real = base64.b64encode(bytes([0x50, 0x54, 0x00, 0x02] + [0] * 40)).decode()
    scenes = {
        "Aurora": Scene("Aurora", 0x4A82, stub),
        "Graffiti": Scene("Graffiti", 0x4A94, real),
        "Sunrise": Scene("Sunrise", 0x4A83, None),  # bare by design
    }
    monkeypatch.setattr(basemod, "load_scenes", lambda sku: scenes)  # type: ignore[attr-defined]

    dev = create_device(BLEDevice("AA:BB:CC:DD:EE:03", "GVH60A6", details={}), "H60A6")
    dev._connection.send = _AsyncMock()  # type: ignore[attr-defined]

    async def go() -> None:
        await dev.set_scene_by_name("Aurora")    # stub -> bare-activate
        n_after_aurora = dev._connection.send.call_count  # type: ignore[attr-defined]
        assert n_after_aurora == 1, "stub scene must be a single bare-activate"
        await dev.set_scene_by_name("Sunrise")   # no param -> bare-activate
        await dev.set_scene_by_name("Graffiti")  # real -> upload (a3 chunks) + activate

    asyncio.run(go())
    frames = [c.args[0] for c in dev._connection.send.call_args_list]  # type: ignore[attr-defined]
    # Aurora + Sunrise: one 33 05 04 each; no a3 frames among them.
    assert frames[0][:3].hex() == "330504"
    assert frames[1][:3].hex() == "330504"
    # Graffiti uploaded: at least one a3 chunk before its final 33 05 04.
    graffiti = frames[2:]
    assert any(f[:1].hex() == "a3" for f in graffiti), "real blob must upload a3 chunks"
    assert graffiti[-1][:3].hex() == "330504"
