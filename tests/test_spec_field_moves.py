"""Library sites that read a field or instance the spec already has, instead of the raw bytes.

From the independent inventory of refactor/spec-client @ 18628d4 ("sites that ignore an existing spec
field or instance"). Each test pins the spec-based reading, and where the spec reads differently from
the old byte code (the signed IC count), the app's reading. No hardware."""
from __future__ import annotations

from govee_ble_local import crypto
from govee_ble_local.transport import handshake
from govee_ble_local.wire import _spec, build, describe, parse, reassemble


def _frame(*payload: int) -> bytes:
    buf = bytearray(20)
    buf[: len(payload)] = bytes(payload)
    x = 0
    for c in buf[:19]:
        x ^= c
    buf[19] = x
    return bytes(buf)


def test_find_reply_matches_on_the_readers_header_even_when_the_body_does_not_parse() -> None:
    """An aa 06 whose text is not UTF-8 reads (version_text decodes only when its text is asked for), and
    it is still the aa 06 reply; its decoder returns None."""
    bad_text = _frame(0xAA, 0x06, 0xEB, 0x13, 0xCF, 0xE8)
    assert _spec.try_read_frame(bad_text, _spec.RX, _spec.PLAIN) is not None
    assert parse.find_reply([_frame(0x33, 0x06), bad_text], 0x06) == bad_text
    assert parse.parse_soft_version(bad_text) is None
    assert parse.find_reply([_frame(0xAA, 0x01, 1)], 0x06) is None


def test_the_ic_count_is_read_signed_as_the_app_reads_it() -> None:
    """ic_segment_read.ic_count_signed (ControllerOnlyReadIcSegmentNum: BleUtil.getSignedShort)."""
    ic = parse.parse_ic_count(_frame(0xAA, 0x40, 0xFF, 0xFF, 0x05))
    assert ic is not None and (ic.ic_count, ic.segment_count) == (-1, 5)
    ic = parse.parse_ic_count(_frame(0xAA, 0x40, 0x00, 0x2E, 0x05))
    assert ic is not None and ic.ic_count == 46


def test_section_count_and_device_info_selector_come_from_the_reader() -> None:
    sc = parse.parse_section_count(_frame(0xAA, 0x41, 0x80, 0x01, 0x05))
    assert sc is not None and (sc.ic_count, sc.section_num) == (-32767, 5)
    assert parse.parse_section_count(_frame(0xAA, 0x40, 0x00, 0x01, 0x05)) is None
    assert parse.parse_device_info_selector(_frame(0xAA, 0x07, 0x03, 0x31)) == 0x03
    assert parse.parse_device_info_selector(_frame(0xAA, 0x06, 0x03)) is None


def test_the_scene_code_reads_as_the_scene_payload() -> None:
    m = parse.mode_reply(_frame(0xAA, 0x05, 0x04, 0x2A, 0x01), _spec.PLAIN)
    assert parse.scene_of(m) == 0x012A


def test_the_0x0b_writer_and_the_status_query_use_spec_types() -> None:
    assert build._rgbic0b_frame((1, 2, 3), 0x1FFF)[:8] == bytes([0x33, 0x05, 0x0B, 1, 2, 3, 0xFF, 0x1F])
    assert build.status_query(full=True)[:6] == bytes([0xAC, 0x03, 0x03, 0x41, 0x30, 0xA5])
    assert build.status_query()[:5] == bytes([0xAC, 0x03, 0x02, 0x41, 0x30])


def test_a_negative_tlv_len_is_the_specs_validation_failure() -> None:
    """status_tlv's len valid (max 0x7f) stops the walk; the analyzer reports it from negative_len."""
    buf = bytes([0x01, 0x01, 0x01, 0x04, 0x80, 0x00])
    tlvs, cut = reassemble.walk_status_tlvs(buf)
    assert [int(t.tag) for t in tlvs] == [0x01]
    assert cut is not None
    assert (cut.reason, cut.type, cut.length, cut.negative_len) == ("unparsed", 0x04, 0x80, True)
    tlvs, cut = reassemble.walk_status_tlvs(bytes([0x01, 0x05, 0x01]))
    assert cut is not None and (cut.reason, cut.negative_len) == ("truncated", False)


def test_a_0x07_block_the_app_skips_is_the_one_status_device_info_leaves_raw() -> None:
    """status_tlv 0x07 is device_info_record on the status path: a selector the app's w() doesn't decode
    (0x03, text not UTF-8) is not read (app_decodes False; its bytes are the state's rest) and the walk
    goes on; a 0x10 block with a short body stops it."""
    skip = bytes([0x07, 0x02, 0x03, 0xFF, 0x01, 0x01, 0x01])
    skipped: list[reassemble.TlvCut] = []
    tlvs, cut = reassemble.walk_status_tlvs(skip, skipped=skipped)
    assert cut is None and [int(t.tag) for t in tlvs] == [0x07, 0x01] and skipped == []
    record = tlvs[0].value.body
    assert record.app_decodes is False and getattr(record, "info", None) is None
    assert bytes(tlvs[0].value.rest.bytes) == b"\xff"
    stop = bytes([0x07, 0x02, 0x10, 0x00])
    _tlvs, cut = reassemble.walk_status_tlvs(stop)
    assert cut is not None and cut.reason == "unparsed"


def test_the_analyzer_labels_unknown_values_from_the_readers_fields() -> None:
    assert describe.describe_frame(_frame(0x33, 0x7E), "tx")[0] == "write/cmd=0x7e"
    assert describe.describe_frame(_frame(0xAA, 0x7E), "rx")[0] == "read/cmd=0x7e"
    assert describe.describe_frame(_frame(0xEE, 0x7E), "rx")[0] == "notify/sub=0x7e"


def test_the_handshake_reply_is_read_by_the_frame_reader() -> None:
    key = bytes(range(16))
    reply = crypto.encrypt(_frame(0xE7, 0x01, *key), handshake.PSK)
    assert handshake.parse_session_key(reply) == key
    assert handshake.is_step2_ack(crypto.encrypt(_frame(0xE7, 0x02), handshake.PSK))
    assert handshake.parse_session_key(crypto.encrypt(_frame(0xAA, 0x01, *key), handshake.PSK)) is None


def test_the_colour_write_mask_cap_is_the_specs_mask_cap() -> None:
    """op15_color's seg_mask cuts the mask at its len_cap: 7 bytes for form kelvin_tint, 10 for kelvin."""
    every = (1 << 120) - 1
    tint = build._op15_color_frame(build._FORM_KELVIN_TINT, (1, 2, 3), every, 8 * 15)
    assert tint[:4] == bytes([0x33, 0x05, 0x15, 0x01]) and tint[12:19] == b"\xff" * 7
    kelvin = build._op15_color_frame(build._FORM_KELVIN, (1, 2, 3), every, 8 * 15, 2700)
    assert kelvin[:4] == bytes([0x33, 0x05, 0x15, 0x01]) and kelvin[9:19] == b"\xff" * 10
