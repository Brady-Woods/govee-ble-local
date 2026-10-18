"""The H5083 plug, driven end to end against its firmware image (spec/firmware.yaml h5083_sw1_00_30).

The peripheral is tests/firmware_sim.py behind the bleak fake (fake_ble.FirmwareGovee). Every frame
and advert it sends is built through the spec, and its answers, pairing state, relay and advert are
the image's. The library under test is the real one: Device, transport, handshake and wire layer.
What the device did is read from the image's answer ids (``sim.answered``), not from bytes. No test
here builds a frame by hand (tests/test_hand_frames_lint.py).
"""
from __future__ import annotations

import pytest

from govee_ble_local import GoveeAdvertisement, create_device
from govee_ble_local.devices.device import Device
from govee_ble_local.exceptions import GoveeBleAuthError
from govee_ble_local.identify import identify
from govee_ble_local.transport import connection as conn_mod

from fake_ble import FirmwareGovee, ble_device

ADDR = "AA:BB:CC:DD:EE:83"
HANDSHAKE = ["e7_session_key", "e7_confirm"]


@pytest.fixture
def plug(monkeypatch: pytest.MonkeyPatch) -> FirmwareGovee:
    fake = FirmwareGovee()
    monkeypatch.setattr(conn_mod, "establish_connection", fake.establish)
    # An unpaired plug leaves 33 b5 (and 33 01) unanswered: don't wait the real ack timeout for it.
    monkeypatch.setattr(conn_mod, "WRITE_ACK_TIMEOUT", 0.1)
    return fake


def _device(plug: FirmwareGovee, **kw: object) -> Device:
    """The H5083 as Home Assistant creates it: from its advert, which picks the encrypted channel."""
    adv = plug.advertisement()
    assert adv is not None
    dev = create_device(ble_device(ADDR, adv.local_name), "H5083", adv, **kw)  # type: ignore[arg-type]
    dev._conn._idle_disconnect = 0
    return dev


async def _paired(plug: FirmwareGovee) -> Device:
    """A device holding the plug's secret, read in the button window and sent (33 b2) by a poll."""
    dev = _device(plug)
    plug.sim.event("button")
    secret = await dev.read_secret()
    assert secret is not None
    dev.set_secret(secret)
    await dev.update()
    assert plug.sim.state.pair_state == 2
    return dev


async def test_pairing_reads_the_secret_inside_the_button_window(plug: FirmwareGovee) -> None:
    # WAS test_device_v3.test_read_secret_bootstrap (an H5080, same goodsType 43 profile, answered by a
    # hand-built aa b1 reply). The unpaired plug answers aa b1 without its secret (selector 0) until its
    # button is pressed; inside the window it returns the secret, which then unlocks the session.
    dev = _device(plug)
    assert await dev.read_secret() is None
    plug.sim.event("button")             # also toggles outlet 0 and pushes aa 01
    secret = await dev.read_secret()
    assert secret is not None and secret == plug.sim.state.secret
    dev.set_secret(secret)
    await dev.set_power(False)
    assert plug.sim.answered[-2:] == ["secret_check", "relay_write"]
    assert plug.sim.state.pair_state == 2 and plug.sim.state.relay0 == 0


async def test_a_wrong_secret_is_an_auth_error(plug: FirmwareGovee) -> None:
    # WAS test_device_transport.test_plug_wrong_secret_is_auth_error (a fake that acked every 33 write
    # with 1). The plug has made no secret yet, so it checks 33 b2 against its fallback and acks 1.
    dev = _device(plug, secret=bytes(8))
    with pytest.raises(GoveeBleAuthError):
        await dev.set_power(True)
    assert plug.sim.answered == [*HANDSHAKE, "secret_check"]
    assert plug.sim.state.pair_state == 0 and plug.sim.state.relay0 == 0


async def test_set_power_switches_the_relay_and_reads_it_back(plug: FirmwareGovee) -> None:
    # WAS test_spec_client_fixes.test_plug_power_frame_per_goods_type[H5083] (the 33 01 11 / 10 bytes)
    # and test_fix_wp3_device_bugs.test_plug_relay_bits_follow_the_detail_screen[H5083-16-False]; their
    # H5080 rows still pin goodsType 43's frame bytes and relay bit. A gt 43 power write is the one
    # frame: no 33 b5 re-sync after it (H5083NewDetailViewModel.y0).
    dev = await _paired(plug)
    for on in (True, False):
        mark = len(plug.sim.answered)
        await dev.set_power(on)
        assert plug.sim.answered[mark:] == ["relay_write"]
        assert plug.sim.state.relay0 == int(on)
        st = await dev.update()
        assert st.is_on is on and st.optimistic is False
        assert plug.sim.answered[-1] == "power_read"


async def test_device_info_is_read_once_per_device(plug: FirmwareGovee) -> None:
    # WAS test_fix_wp3_review.test_device_info_one_shot_is_used_up_by_a_responsive_plug_poll and
    # test_device_info_reads.test_device_info_read_sends_only_the_firmware_backed_reads[H5083] (live
    # captures; the other SKUs' captures still pin the reply layouts). The first poll reads aa 06,
    # aa 07 03 and aa 07 02 after the relay, and no later poll reads them again. Unpaired, the plug
    # leaves the session's 33 b5 unanswered.
    dev = _device(plug)
    st = await dev.update()
    assert plug.sim.answered == [*HANDSHAKE, "power_read", "soft_version", "hard_version", "device_id"]
    assert (st.firmware_version, st.hardware_version) == (plug.sim.image.soft_version,
                                                          plug.sim.image.hard_version)
    # SnController: the reply carries the id reversed, printed as colon-separated hex
    assert st.device_id == ":".join(f"{b:02X}" for b in reversed(plug.sim.state.device_id))
    assert st.is_on is False and dev._device_info_read is True
    mark = len(plug.sim.answered)
    await dev.update()
    assert plug.sim.answered[mark:] == ["power_read"]


async def test_the_advert_identifies_the_plug_and_carries_its_relay(plug: FirmwareGovee) -> None:
    # WAS test_identify.test_identify_encrypted_device and the H5083 lines of
    # test_identify_unparseable_pact_is_plaintext_unknown (a hand-built manufacturer value under header
    # 0x41), and the H5083 rows of test_fix_wp3_review.test_plug_advert_on_off_follows_the_app; its H5080
    # rows keep goodsType 43's mask (Model.A: (b & 1) == 1) on switch bytes the H5083 never sends.
    adv = plug.advertisement()
    assert adv is not None
    found = identify(adv.local_name, adv.manufacturer_data)
    assert found == GoveeAdvertisement("H5083", adv.local_name, True, 3, 2, 2)
    assert found.pact == (2, 2) and found.encryption_known is True
    dev = _device(plug)
    for _ in range(2):
        plug.sim.event("button")         # toggles outlet 0; the advert's switch byte follows it
        assert dev.ingest_advertisement(plug.advertisement()) is True  # type: ignore[arg-type]
        assert dev.state.is_on is bool(plug.sim.state.relay0)
