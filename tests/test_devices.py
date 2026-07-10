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


def test_scene_upload_params_mapping() -> None:
    """Path-B commByte/strip selection from (sceneType, versionArray)."""
    from govee_ble_local.scenes import scene_upload_params as p

    all_v = frozenset({0, 1, 2, 3, 6})
    assert p(1, all_v) == (1, 0)   # rgb  -> V1, comType 1, strip 0
    assert p(2, all_v) == (2, 0)   # rgbic -> V2
    assert p(3, all_v) == (7, 2)   # graffiti -> V3, strip 2
    assert p(6, all_v) == (10, 1)  # compose  -> V6, comType 10, strip 1
    assert p(0, all_v) is None     # static -> activate-only
    assert p(4, all_v) is None     # cube
    assert p(5, all_v) is None     # DIY -> family-specific (per-device override), not this table
    assert p(None, all_v) is None
    # gated on the device supporting the required scene-version (strict AND-gate)
    assert p(2, frozenset({1})) is None    # V2 not supported
    assert p(6, frozenset({1, 2})) is None  # V6 not supported


def test_scene_upload_combyte_per_device() -> None:
    """Each curated SKU emits the right dialect-A commByte @ a3_start byte4 (source Q1-Q4)."""
    import base64

    from govee_ble_local.scenes import Scene

    def frames(sku: str, scene: Scene) -> list[bytes] | None:
        dev = create_device(BLEDevice("AA:BB:CC:DD:EE:10", sku, details={}), sku)
        return dev._scene_upload_frames(scene)  # type: ignore[attr-defined]

    def param(*head: int) -> str:
        return base64.b64encode(bytes(list(head) + [0] * 30)).decode()

    # bulbs: type-1 rgb -> comByte 1 (Q1: apply path forces version 1)
    f = frames("H6006", Scene("x", 1, param(0x83), scene_type=1))
    assert f and f[0][0] == 0xA3 and f[0][4] == 0x01
    assert frames("H6008", Scene("x", 1, param(0x83), scene_type=1))[0][4] == 0x01  # type: ignore[index]
    # rgbic strips: type-2 -> comByte 2
    for sku in ("H6047", "H6641", "H61A8"):
        f = frames(sku, Scene("x", 1, param(0x54, 0, 0, 2), scene_type=2))
        assert f and f[0][4] == 0x02, sku
    # H6052 type-5 (byte0 0x13) -> comByte 9, strip 1; type-3 -> activate-only (no version 3)
    f = frames("H6052", Scene("x", 1, param(0x13, 1, 2, 3), scene_type=5))
    assert f and f[0][0] == 0xA3 and f[0][4] == 0x09
    assert frames("H6052", Scene("x", 1, param(0x01, 0, 0), scene_type=3)) is None


def test_scene_by_name_uploads_per_scene_type(monkeypatch: object) -> None:
    """Base **dialect-A** routing (tested on H61A8, a pure dialect-A device):
    set_scene_by_name always activates (33 05 04), and uploads an a3 burst only for
    scene-types with a resolved commByte whose version the device supports. H61A8's
    versionArray is {1,2}, so an rgbic (sceneType 2) scene uploads commByte 2 @ a3_start
    byte 4, while sceneType 5 (no dialect-A branch) and 0 (static) stay activate-only.
    (H60A6's dialect-B type-5 routing is covered in test_dialect_b_upload.py.)"""
    import base64
    from unittest.mock import AsyncMock as _AsyncMock

    from govee_ble_local.devices import base as basemod
    from govee_ble_local.scenes import Scene

    param = base64.b64encode(bytes([0x50, 0x54, 0x00, 0x02] + [0] * 40)).decode()
    scenes = {
        "Rgbic": Scene("Rgbic", 0x4A94, param, scene_type=2),   # V2 supported -> upload comm 2
        "Diy": Scene("Diy", 0x4A82, param, scene_type=5),       # no dialect-A branch -> activate-only
        "Static": Scene("Static", 0x4A83, param, scene_type=0),  # no branch -> activate-only
        "Sunrise": Scene("Sunrise", 0x4A84, None),               # no param -> activate-only
    }
    monkeypatch.setattr(basemod, "load_scenes", lambda sku: scenes)  # type: ignore[attr-defined]

    dev = create_device(BLEDevice("AA:BB:CC:DD:EE:03", "GVH61A8", details={}), "H61A8")

    # sceneType 5 / 0 / no-param -> activate-only (no a3 frames).
    for name in ("Diy", "Static", "Sunrise"):
        dev._connection.send = _AsyncMock()  # type: ignore[attr-defined]
        asyncio.run(dev.set_scene_by_name(name))
        frames = [c.args[0] for c in dev._connection.send.call_args_list]  # type: ignore[attr-defined]
        assert frames and all(f[:3].hex() == "330504" for f in frames), name
        assert not any(f[:1].hex() == "a3" for f in frames), f"{name} must not upload"

    # sceneType 2 (rgbic), V2 in versionArray -> upload a3 (comm 2 @ byte4) then activate.
    dev._connection.send = _AsyncMock()  # type: ignore[attr-defined]
    asyncio.run(dev.set_scene_by_name("Rgbic"))
    up = [c.args[0] for c in dev._connection.send.call_args_list]  # type: ignore[attr-defined]
    a3 = [f for f in up if f[:1].hex() == "a3"]
    assert a3, "rgbic scene must upload a3 chunks"
    assert a3[0][1] == 0x00 and a3[0][2] == 0x01, "a3_start: seq 0, marker 0x01"
    assert a3[0][4] == 0x02, "commByte 2 at a3_start byte 4"
    assert up[-1][:3].hex() == "330504", "activation follows the upload"


def test_scene_sets_optimistic_state_and_color_clears_it(monkeypatch: object) -> None:
    """set_scene reflects the scene immediately (active_scene), and switching to
    a solid colour clears scene_code so HA shows the right effect without waiting
    for a poll."""
    from unittest.mock import AsyncMock as _AsyncMock

    from govee_ble_local.devices import base as basemod
    from govee_ble_local.scenes import Scene

    monkeypatch.setattr(  # type: ignore[attr-defined]
        basemod, "load_scenes", lambda sku: {"Rainbow": Scene("Rainbow", 0x4A85, None)}
    )
    dev = create_device(BLEDevice("AA:BB:CC:DD:EE:04", "GVH60A6", details={}), "H60A6")
    dev._connection.send = _AsyncMock()  # type: ignore[attr-defined]

    async def go() -> None:
        await dev.set_scene(0x4A85)  # type: ignore[attr-defined]
        assert dev.state.scene_code == 0x4A85
        assert dev.state.is_on is True
        assert dev.active_scene == "Rainbow"  # type: ignore[attr-defined]
        await dev.set_rgb((255, 0, 0))  # type: ignore[attr-defined]
        assert dev.state.scene_code is None  # solid colour exits scene mode
        assert dev.active_scene is None  # type: ignore[attr-defined]

    asyncio.run(go())
