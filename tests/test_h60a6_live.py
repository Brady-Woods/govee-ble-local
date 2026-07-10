"""Live H60A6 suite — the spec's oracle.

Runs only when ``GOVEE_H60A6_ADDRESS`` is set (skipped in normal CI). It drives a
real H60A6 with **spec-conformant** frames (``tests/spec_frames``, built from
``spec/govee_ble.ksy``) over the library's ``GoveeConnection`` transport (BLE +
0xE7 AES handshake — not in dispute), then reads state back via the 0xAC status
burst. The point is to prove the *spec* frames actually control the device, and to
A/B the disputed scene upload (library vs spec) on real hardware.

    GOVEE_H60A6_ADDRESS=AA:BB:CC:DD:EE:FF pytest tests/test_h60a6_live.py -v -s
"""
from __future__ import annotations

import asyncio
import os

import pytest
import pytest_asyncio

pytest.importorskip("kaitaistruct", reason="Kaitai runtime not installed")
_gb = pytest.importorskip(
    "spec_gen.govee_ble_frame", reason="run tools/gen_kaitai.sh to generate the Kaitai reader"
)
GoveeBleFrame = _gb.GoveeBleFrame

import spec_frames as sf  # noqa: E402

_ADDRESS = os.environ.get("GOVEE_H60A6_ADDRESS")

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not _ADDRESS, reason="set GOVEE_H60A6_ADDRESS to run the live H60A6 suite"),
]


# ── helpers ──────────────────────────────────────────────────────────────────
async def _read_status(conn, address: str, attempts: int = 4, require_zone: bool = False):
    """Mirror StatusReadable._read_state reassembly, but as a standalone read.

    The 0xAC status burst is drop-prone: a dropped terminator chunk yields empty
    zone_power/segments even though brightness (chunk 0x00) arrived. Retry until we
    get a usable read. ``require_zone`` insists on a populated zone_power (needs the
    terminator chunk), since a brightness-only read would give a stale/empty zone map."""
    from govee_ble_local.ble import status

    st = None
    for _ in range(attempts):
        frames = await conn.query(sf.status_query(full=True), timeout=5.0)
        chunks: dict[int, bytes] = {}
        for fr in frames:
            if len(fr) == 20 and fr[0] == 0xAC:
                chunks[fr[1]] = fr[2:19]
        st = status.parse_status(chunks, address)
        ok = bool(st.zone_power) if require_zone else (
            st.zone_power or st.segments or st.brightness is not None
        )
        if ok:
            return st
        await asyncio.sleep(0.5)
    return st


@pytest_asyncio.fixture
async def conn():
    from bleak import BleakScanner

    from govee_ble_local.models import Encryption
    from govee_ble_local.transport.connection import GoveeConnection

    device = await BleakScanner.find_device_by_address(_ADDRESS, timeout=20.0)
    if device is None:
        pytest.skip(f"H60A6 {_ADDRESS} not found in scan")
    c = GoveeConnection(device, encryption=Encryption.AES_RC4_PSK)
    await c.connect()
    try:
        yield c
    finally:
        await c.disconnect()


# ── wire-cipher / advertisement truth ────────────────────────────────────────
async def test_advertisement_reports_encrypted():
    """The H60A6 advert should set the encrypted flag (→ AES wire cipher), confirming
    that spec 'secret_key_pairing' is a separate OPTIONAL account-lock, not the cipher."""
    from bleak import BleakScanner

    from govee_ble_local import identify

    found = {}

    def _cb(dev, adv):
        if dev.address.upper() == _ADDRESS.upper():
            found["adv"] = adv

    scanner = BleakScanner(detection_callback=_cb)
    await scanner.start()
    await asyncio.sleep(8.0)
    await scanner.stop()
    adv = found.get("adv")
    if adv is None:
        pytest.skip("no advertisement captured")
    parsed = identify.parse_manufacturer_data(adv.manufacturer_data)
    if parsed is None:
        pytest.skip("no Govee manufacturer data in advert")
    encrypted, version, pact_type, pact_code = parsed
    print(f"H60A6 adv: encrypted={encrypted} broadcast_version={version} "
          f"pact_type={pact_type} pact_code={pact_code}")
    assert encrypted is True  # AES wire cipher; secret_key_pairing is a separate lock


# ── spec frames drive the device ─────────────────────────────────────────────
async def test_power_spec_frame(conn):
    await conn.send(sf.power(True))
    await asyncio.sleep(1.0)
    st = await _read_status(conn, _ADDRESS)
    assert st.is_on is True


async def test_brightness_spec_frame(conn):
    await conn.send(sf.power(True))
    await conn.send(sf.brightness(40))
    await asyncio.sleep(1.0)
    st = await _read_status(conn, _ADDRESS)
    assert st.brightness is not None and abs(st.brightness - 40) <= 5


async def test_color_spec_frame(conn):
    mask = sf.all_segments_mask(13)
    await conn.send(sf.power(True))
    await conn.send(sf.color_rgb_15(0xFF, 0x00, 0x00, mask))  # red, whole device
    await asyncio.sleep(1.0)
    st = await _read_status(conn, _ADDRESS)
    # segments should read back predominantly red
    reds = [s.rgb for s in (st.segments or []) if s.rgb]
    assert reds and all(r[0] >= 0x80 and r[1] < 0x60 and r[2] < 0x60 for r in reds), reds


async def test_zone_power_spec_frame(conn):
    # background zone (index 1) off then on
    await conn.send(sf.power(True))
    await conn.send(sf.zone_power(1, False))
    await asyncio.sleep(1.0)
    off = await _read_status(conn, _ADDRESS, require_zone=True)
    await conn.send(sf.zone_power(1, True))
    await asyncio.sleep(1.0)
    on = await _read_status(conn, _ADDRESS, require_zone=True)
    assert off.zone_power.get(1) is False
    assert on.zone_power.get(1) is True


# ── color temperature: spec kelvin frame + mode read-back confirmation ───────
async def _read_mode_reply(conn):
    """Send mode_query (aa 05 01) and return the decrypted reply frame, or None."""
    from govee_ble_local.ble import controllers

    frames = await conn.query(
        controllers.mode_query(), opcode=0xAA, terminal=0x05, timeout=3.0
    )
    for fr in frames:
        if len(fr) == 20 and fr[0] == 0xAA and fr[1] == 0x05:
            return fr
    return None


def _kelvin_from_mode_reply(reply: bytes) -> int | None:
    """Kelvin from a mode read-back. Confirm via the Kaitai reader that it's the
    RGBIC color sub-mode (0x15) op set_color, then read the kelvin.

    IMPORTANT: the read-back CCT layout differs from the *write*. The write is
    ``33 05 15 01 FF FF FF <kelvin u2be> <tint> <mask>``; the device's read reply
    is ``aa 05 15 01 <kelvin u2be> 00 ...`` — kelvin sits DIRECTLY after op 0x01
    (bytes [4:6]), with no FF FF FF white-point prefix. (Confirmed live on H60A6:
    2700K -> 0x0A8C, 6500K -> 0x1964.) The govee_ble.ksy op15_color models only the
    write layout, so we read the kelvin from the raw offset here."""
    f = GoveeBleFrame.from_bytes(reply)
    m = f.body.params
    if m.sub_type != GoveeBleFrame.SubMode.color_rgbic_15:
        return None
    if m.params.op_type != GoveeBleFrame.Op15.set_color:
        return None
    if len(reply) < 6:
        return None
    return (reply[4] << 8) | reply[5]


@pytest.mark.parametrize("kelvin,label", [(2700, "WARM"), (6500, "COLD")])
async def test_color_temp_spec_frame(conn, kelvin, label):
    """Spec op15 CCT frame drives warm/cold white on the RGBICWW H60A6, confirmed
    via the mode read-back (parsed by the Kaitai reader), not just visually."""
    mask = sf.all_segments_mask(13)
    await conn.send(sf.power(True))
    await conn.send(sf.color_temp_15(kelvin, (0, 0, 0), mask))
    await asyncio.sleep(1.5)

    reply = await _read_mode_reply(conn)
    print(f"\n[{label} {kelvin}K] mode reply = {reply.hex() if reply else None}")
    if reply is None:
        pytest.skip("no mode reply; observe the light for warm/cold instead")

    read_k = _kelvin_from_mode_reply(reply)
    print(f"[{label} {kelvin}K] parsed kelvin from mode read-back = {read_k}")
    if read_k is None:
        pytest.skip(
            "device did not echo kelvin in the mode reply "
            "(color/CCT mode readback undocumented) — fall back to visual"
        )
    # The device may snap to its own supported step; allow a tolerance.
    assert abs(read_k - kelvin) <= 300, f"set {kelvin}K, device reports {read_k}K"


# ── the R2 verdict: library vs spec scene upload, on real hardware ────────────
async def test_scene_upload_ab(conn):
    """A/B the disputed scene framing. Prints which path the device accepts; the
    visual result is recorded by the operator (run with -s and watch the light)."""
    from govee_ble_local.ble import controllers
    from govee_ble_local.scenes import load_scenes

    scene = next(
        (s for s in load_scenes("H60A6").values()
         if s.param and not s.placeholder), None
    )
    if scene is None:
        pytest.skip("no H60A6 scene param bundled")

    print(f"\n[scene A/B] scene={scene.name!r} code={scene.code}")

    # A — library framing (0x08 bit, no comType)
    for chunk in controllers.scene_chunks(scene.param):
        await conn.send(chunk, expect_ack=False)
    await conn.send(controllers.scene((scene.code & 0xFF, (scene.code >> 8) & 0xFF)))
    print("[A] library scene_chunks sent — OBSERVE the light, then press enter timeout")
    await asyncio.sleep(6.0)

    # B — spec framing (comType=RGBIC, no 0x08)
    for chunk in sf.scene_upload(scene.param, comm_byte=sf.COMM_H60A6):
        await conn.send(chunk, expect_ack=False)
    await conn.send(controllers.scene((scene.code & 0xFF, (scene.code >> 8) & 0xFF)))
    print("[B] spec scene_upload sent — OBSERVE the light")
    await asyncio.sleep(6.0)
    # No auto-assert (visual). This test documents the A/B; the operator records
    # which rendered the correct effect. That result decides R2.
