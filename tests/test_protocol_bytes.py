"""Byte-exactness tests for crypto, framing, and command builders.

Fixtures are real bytes captured from Govee devices / the app.
"""
from __future__ import annotations

from govee_ble_local import crypto
from govee_ble_local.ble import controllers, frame
from govee_ble_local.const import PSK
from govee_ble_local.transport import handshake


def test_checksum_and_frame() -> None:
    f = frame.build_frame(0x33, 0x01, bytes([0x11]))
    assert len(f) == 20
    assert f[:3] == bytes([0x33, 0x01, 0x11])
    assert crypto.checksum_ok(f)
    assert frame.split_frame(f)[:2] == (0x33, 0x01)


def test_crypto_roundtrip() -> None:
    f = frame.build_frame(0x33, 0x05, bytes([0x0D, 1, 2, 3]))
    assert crypto.decrypt(crypto.encrypt(f, PSK), PSK) == f


def test_decrypt_real_handshake_reply() -> None:
    # Real PSK-encrypted e7-01 reply captured from an H5083 (btmon).
    reply = bytes.fromhex("3a46b9a6d72160f4d63bd8bf9c57d22369c040e6")
    pt = crypto.decrypt(reply, PSK)
    assert pt[0] == 0xE7 and pt[1] == 0x01
    assert crypto.checksum_ok(pt)
    key = handshake.parse_session_key(reply)
    assert key == pt[2:18]


def test_secret_check_matches_capture() -> None:
    # Real app frame: 33 b2 <8-byte secret> <pad> <bcc>.
    got = controllers.secret_check(bytes.fromhex("615521090b735c54"))
    assert got.hex() == "33b2615521090b735c54000000000000000000ed"


def test_sync_time_matches_capture() -> None:
    # Real app frame: 33 b5 6a 49 90 1a 01 f9 ... d7
    got = controllers.sync_time(0x6A49901A)
    assert got.hex() == "33b56a49901a01f90000000000000000000000d7"


def test_power_relay_and_binary() -> None:
    assert controllers.power(True, relay=True)[:3].hex() == "330111"
    assert controllers.power(False, relay=True)[:3].hex() == "330110"
    assert controllers.power(True)[:3].hex() == "330101"
    assert controllers.power(False)[:3].hex() == "330100"


def test_brightness() -> None:
    assert controllers.brightness(50)[:3].hex() == "330432"
    assert controllers.brightness(200)[:3].hex() == "330464"  # clamped to 100


def test_rgb_layouts() -> None:
    # h6006 scheme: 33 05 0d r g b  (confirmed vs H6006 capture)
    assert controllers.rgb(255, 0, 0, "h6006")[:6].hex() == "33050dff0000"
    # h60a6 scheme: 33 05 15 01 r g b 00000 ff 1f
    h = controllers.rgb(0, 255, 0, "h60a6")
    assert h[:7].hex() == "3305150100ff00"
    assert h[12:14].hex() == "ff1f"


def test_color_temp_h6006_matches_java() -> None:
    # Exact port of tablelampv1.SubModeColor: 33 05 0d ff ff ff <k_hi> <k_lo>
    # 00 00 00  (WHITE in the RGB slot, raw Kelvin, no tint for out-of-table K).
    f = controllers.color_temp(2700, "h6006")
    assert f[:11].hex() == "33050dffffff0a8c000000"  # 2700 = 0x0A8C


def test_scene() -> None:
    assert controllers.scene((0x00, 0x64))[:5].hex() == "3305040064"


# --- advertisement identification (BleUtil.parseBleBroadcastPact) -----------
from govee_ble_local import identify  # noqa: E402


def test_identify_encrypted_vs_plaintext_from_real_adv() -> None:
    # Real advertised (company_id -> mfg value) pairs from live scans.
    # 0x8843 header low byte 0x43 has bit 0x40 -> encrypted; 0x8802/0x8801 don't.
    h60a6 = identify.identify("GVH60A67457", {0x8843: bytes.fromhex("ec0001030100")})
    assert h60a6 is not None and h60a6.encrypted is True
    h5083 = identify.identify("ihoment_H5083_A2D1", {0x8843: bytes.fromhex("ec00020200")})
    assert h5083 is not None and h5083.sku == "H5083" and h5083.encrypted is True
    h6006 = identify.identify("ihoment_H6006_60AF", {0x8802: bytes.fromhex("ec00010101")})
    assert h6006 is not None and h6006.encrypted is False
    h61a8 = identify.identify("Govee_H61A8_631F", {0x8802: bytes.fromhex("ec00020201")})
    assert h61a8 is not None and h61a8.sku == "H61A8" and h61a8.encrypted is False


def test_sku_from_name() -> None:
    assert identify.sku_from_local_name("ihoment_H5083_A2D1") == "H5083"
    assert identify.sku_from_local_name("Govee_H61A8_631F") == "H61A8"
    assert identify.sku_from_local_name("GVH60A67457") == "H60A6"


def test_identify_rejects_non_govee() -> None:
    assert identify.identify("SomeOtherDevice", {0x1234: b"\x00\x01"}) is None
