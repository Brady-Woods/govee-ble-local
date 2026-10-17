"""Integration fix-up: the cross-lane requests and the last review minors.

Single-reply reads ignore the burst idle gap, a pending unlock survives a cancelled write, the
session cipher, shared constants, the public GoveeBleUnknownName, the frame-log flush, the status
walk's signed len byte, and the analyzer CLI's truncated / redacted counts and frames-log prefix."""
from __future__ import annotations

import asyncio
import importlib
import json
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest.mock import AsyncMock

import pytest
from bleak.exc import BleakCharacteristicNotFoundError
from conftest import require_optional

require_optional("kaitaistruct", reason="Kaitai runtime not installed")

import govee_ble_local  # noqa: E402
from govee_ble_local import const, debug  # noqa: E402
from govee_ble_local.const import WRITE_CHAR_UUID  # noqa: E402
from govee_ble_local.crypto import encrypt  # noqa: E402
from govee_ble_local.devices.device import Device, make_device  # noqa: E402
from govee_ble_local.exceptions import (  # noqa: E402
    GoveeBleAuthError,
    GoveeBleCommandRejected,
    GoveeBleConnectionError,
    GoveeBleHandshakeConnectionError,
    GoveeBleInvalidValue,
    GoveeBleNotSupported,
    GoveeBleTimeout,
    GoveeBleUnknownName,
)
from govee_ble_local.models import Encryption  # noqa: E402
from govee_ble_local.scanner import match  # noqa: E402
from govee_ble_local.transport import connection as conn_mod  # noqa: E402
from govee_ble_local.transport import framelog  # noqa: E402
from govee_ble_local.transport.connection import GoveeConnection  # noqa: E402
from govee_ble_local.wire import build, parse  # noqa: E402
from govee_ble_local.wire import reassemble as r  # noqa: E402
from govee_ble_local.wire.describe import status_burst_report  # noqa: E402

from fake_ble import SESSION_KEY, FakeClient, FakeGovee, ble_device, frame  # noqa: E402

ADDR = "AA:BB:CC:DD:EE:F1"
_TOOLS = Path(__file__).resolve().parent.parent / "tools"


@pytest.fixture(autouse=True)
def fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(conn_mod, "WRITE_ACK_TIMEOUT", 0.05)
    monkeypatch.setattr(conn_mod, "HANDSHAKE_STEP2_TIMEOUT", 0.05)
    monkeypatch.setattr(conn_mod, "HANDSHAKE_TIMEOUT", 0.1)


def _conn(monkeypatch: pytest.MonkeyPatch, fake: FakeGovee) -> GoveeConnection:
    monkeypatch.setattr(conn_mod, "establish_connection", fake.establish)
    enc = Encryption.AES_RC4_PSK if fake.encrypted else Encryption.NONE
    c = GoveeConnection(ble_device(ADDR, "GVH60A6X"), encryption=enc)
    c._idle_disconnect = 0
    return c


def _chunk(data: bytes) -> list[bytes]:
    """A TLV stream as the device chunks it (AbsController4BleSingleSendMultiBack.g / .i / .h)."""
    if len(data) <= 12:
        return [frame(0xAC, 0x00, 1, 0, len(data), 0x03, 0x00, *data)]
    rest = data[12:]
    pieces = [rest[i:i + 17] for i in range(0, len(rest), 17)]
    total = 1 + len(pieces)
    out = [frame(0xAC, 0x00, total, 0, len(pieces[-1]), 0x03, 0x00, *data[:12])]
    out += [frame(0xAC, i, *piece) for i, piece in enumerate(pieces[:-1], start=1)]
    out.append(frame(0xAC, 0xFF, *pieces[-1]))
    return out


# ── WP1a-028: a single-reply read is bounded by INFO_READ_TIMEOUT, not the burst idle gap ──────
async def test_read_reply_asks_for_no_idle_gap() -> None:
    d = make_device(ble_device(ADDR, "H6008"), "H6008")
    assert d is not None
    d._conn.query = AsyncMock(return_value=[frame(0xAA, 0x05, 0x0D, 1, 2, 3)])  # type: ignore[method-assign]
    assert await d._read_reply(build.mode_query()) == frame(0xAA, 0x05, 0x0D, 1, 2, 3)
    assert d._conn.query.await_args.kwargs["idle_gap"] is None  # type: ignore[attr-defined]


async def test_a_stray_read_reply_does_not_cut_the_wait_short(monkeypatch: pytest.MonkeyPatch) -> None:
    # A late aa 01 (the reply to an earlier read) arrives first; the real aa 05 reply comes after
    # BURST_IDLE_GAP (0.6 s) but within INFO_READ_TIMEOUT (2 s). WAS: the stray frame armed the idle
    # gap, the read gave up at 0.6 s and returned None.
    fake = FakeGovee(encrypted=False)
    reply = frame(0xAA, 0x05, 0x0D, 0xFF, 0x00, 0x00)

    def on_write(client: FakeClient, plain: bytes) -> None:
        if plain[:2] == b"\xaa\x05":
            client.push(frame(0xAA, 0x01, 0x01), 0.005)
            client.push(reply, const.BURST_IDLE_GAP + 0.2)

    fake.on_write = on_write
    monkeypatch.setattr(conn_mod, "establish_connection", fake.establish)
    d = make_device(ble_device(ADDR, "H6008"), "H6008", idle_disconnect=0)
    assert d is not None
    assert await d._read_reply(build.mode_query()) == reply
    await d._conn.close()


# ── WP3-081: the unlock for a secret set on a live session stays pending until it is written ──
def _plug(monkeypatch: pytest.MonkeyPatch, send: Any, up: dict[str, bool]) -> Device:
    monkeypatch.setattr(GoveeConnection, "is_connected", property(lambda _self: up["v"]))
    d = make_device(ble_device(ADDR, "H5083"), "H5083")
    assert d is not None
    d._conn.send = AsyncMock(side_effect=send)  # type: ignore[method-assign]
    d._conn.send_many = AsyncMock()  # type: ignore[method-assign]
    return d


async def test_a_cancelled_unlock_write_is_sent_again_first(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[bytes] = []
    hold = asyncio.Event()
    first = {"v": True}

    async def send(frame_: bytes, **_k: object) -> None:
        sent.append(frame_)
        if frame_[1] == build.CMD_SECRET_WRITE and first["v"]:
            first["v"] = False
            await hold.wait()          # never set: the caller is cancelled mid-write

    d = _plug(monkeypatch, send, {"v": True})
    secret = bytes(range(1, 9))
    d.set_secret(secret)
    task = asyncio.create_task(d.set_power(True))
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert d._unlock_pending is True          # WAS: False, and the next setter went out unlocked
    sent.clear()
    await d.set_power(True)
    assert [f[:2].hex() for f in sent][:1] == ["33b2"] and sent[0] == build.secret_check(secret)
    assert d._unlock_pending is False


@pytest.mark.parametrize(("error", "up_after", "pending"), [
    (GoveeBleTimeout("no ack"), True, False),              # unacked: treated as sent, as at setup
    (GoveeBleConnectionError("dropped"), False, False),    # session gone: the next setup sends it
    (GoveeBleConnectionError("write failed"), True, True),  # still up: try again next operation
])
async def test_an_unlock_write_that_failed_is_pending_only_on_a_live_session(
    monkeypatch: pytest.MonkeyPatch, error: Exception, up_after: bool, pending: bool,
) -> None:
    up = {"v": True}

    async def send(frame_: bytes, **_k: object) -> None:
        if frame_[1] == build.CMD_SECRET_WRITE:
            up["v"] = up_after
            raise error

    d = _plug(monkeypatch, send, up)
    d.set_secret(bytes(range(1, 9)))
    if isinstance(error, GoveeBleTimeout):
        await d._send_pending_unlock()
    else:
        with pytest.raises(type(error)):
            await d._send_pending_unlock()
    assert d._unlock_pending is pending


async def test_a_rejected_unlock_on_a_live_session_is_an_auth_error_and_not_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def send(frame_: bytes, **_k: object) -> None:
        if frame_[1] == build.CMD_SECRET_WRITE:
            raise GoveeBleCommandRejected("rejected")

    d = _plug(monkeypatch, send, {"v": True})
    d.set_secret(bytes(range(1, 9)))
    with pytest.raises(GoveeBleAuthError, match="rejected the secret key"):
        await d._send_pending_unlock()
    assert d._unlock_pending is False           # the device answered: not sent again


async def test_a_secret_changed_during_the_unlock_write_stays_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    d: Device

    async def send(frame_: bytes, **_k: object) -> None:
        if frame_[1] == build.CMD_SECRET_WRITE and frame_ == build.secret_check(bytes(range(1, 9))):
            d.set_secret(bytes(range(2, 10)))      # a newer secret arrives mid-write

    d = _plug(monkeypatch, send, {"v": True})
    d.set_secret(bytes(range(1, 9)))
    await d._send_pending_unlock()
    assert d._unlock_pending is True               # the newer secret still goes out next


# ── WP1b-003: the session cipher is built once per session, byte-identical to crypto.encrypt ──
async def test_session_cipher_follows_the_session_key(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeGovee()
    c = _conn(monkeypatch, fake)
    assert c._session_cipher is None
    await c.send(frame(0x33, 0x01, 0x01))
    assert c._session_key == SESSION_KEY and c._session_cipher is not None
    assert fake.received[-1] == frame(0x33, 0x01, 0x01)          # the fake decrypts with crypto.decrypt
    assert fake.clients[0].writes[-1] == encrypt(frame(0x33, 0x01, 0x01), SESSION_KEY)
    await c.disconnect()
    assert c._session_key is None and c._session_cipher is None


# ── WP1a-004 / WP2b-013: shared constants ─────────────────────────────────────────────────────
def test_transport_constants_come_from_const_and_build() -> None:
    assert conn_mod.DISCONNECT_TIMEOUT == const.DISCONNECT_TIMEOUT == 5.0
    assert conn_mod.QUERY_TERMINAL == const.QUERY_TERMINAL == 0xFF
    assert (build.PRO_NOTIFY, build.PRO_MULTI_A1) == (0xEE, 0xA1)
    assert parse._ProType.notify.value == build.PRO_NOTIFY
    assert parse._ProType.multi_write.value == build.PRO_MULTI_A1
    assert {build.PRO_NOTIFY, build.PRO_MULTI_A1} <= conn_mod._KNOWN_RX_PRO_TYPES


# ── WP1a-019: an e7 write error is worded neutrally ──────────────────────────────────────────
async def test_handshake_write_error_is_not_called_a_link_loss(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeGovee()
    fake.write_error = BleakCharacteristicNotFoundError(WRITE_CHAR_UUID)   # e.g. a stale service cache
    c = _conn(monkeypatch, fake)
    with pytest.raises(GoveeBleHandshakeConnectionError) as info:
        await c.connect()
    assert "handshake write failed: Characteristic" in str(info.value)
    assert "link lost" not in str(info.value)


# ── WP3-036: GoveeBleUnknownName is public ──────────────────────────────────────────────────
def test_unknown_name_error_is_public_and_both_kinds() -> None:
    assert "GoveeBleUnknownName" in govee_ble_local.__all__
    assert govee_ble_local.GoveeBleUnknownName is GoveeBleUnknownName
    assert issubclass(GoveeBleUnknownName, GoveeBleNotSupported)
    assert issubclass(GoveeBleUnknownName, GoveeBleInvalidValue)
    d = make_device(ble_device(ADDR, "H60A6"), "H60A6")
    assert d is not None
    with pytest.raises(GoveeBleUnknownName, match="unknown zone"):
        d._zone("nope")


# ── WP3-014: scanner.match uses the shared advertisement normalisation ───────────────────────
def test_match_falls_back_to_the_ble_device_name() -> None:
    class _Adv:
        local_name = None
        manufacturer_data = None   # no manufacturer data: identified by name alone
        rssi = -60

    hit = match(ble_device(ADDR, "ihoment_H6008_1A2B"), _Adv())  # type: ignore[arg-type]
    assert hit is not None and hit.sku == "H6008" and hit.rssi == -60 and hit.supported


# ── WP3-060: one scheme-keyed entry point for the 0x0d report ──────────────────────────────
def test_mode_0d_for_picks_the_scheme_decoder() -> None:
    cct = frame(0xAA, 0x05, 0x0D, 0xFF, 0xD5, 0xA1, 0x0F, 0xA0, 1, 2, 3)
    assert parse.parse_mode_0d_for(cct, "h6188") == parse.parse_mode_0d_rgblight(cct) == (None, 4000)
    assert parse.parse_mode_0d_for(cct, "h6006") == parse.parse_mode_0d(cct) == (None, 4000)
    short = cct[:8]   # both read the spec's kind 2, which needs the 8-byte body (a reply is 20 bytes)
    assert parse.parse_mode_0d_for(short, "h6006") is None
    assert parse.parse_mode_0d_for(short, "h6188") is None


# ── WP5a-016: a public frame-log flush ───────────────────────────────────────────────────────
def test_framelog_flush_drains_or_says_it_could_not(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "f.jsonl"
    framelog.FrameLog(str(path), "A").record("tx", wire=b"\x01")
    assert framelog.flush(5.0) is True
    assert [json.loads(x)["wire"] for x in path.read_text().splitlines()] == ["01"]

    class Dead:
        def is_alive(self) -> bool:
            return False

    monkeypatch.setattr(framelog, "_writer", Dead())
    monkeypatch.setattr(framelog._QUEUE, "unfinished_tasks", 1, raising=False)
    assert framelog.flush(5.0) is False           # nothing would drain it: return at once


def test_field_test_runner_uses_the_public_flush(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.syspath_prepend(str(_TOOLS))
    runner = importlib.import_module("field_test.runner")
    calls: list[float] = []
    monkeypatch.setattr(framelog, "flush", lambda timeout: calls.append(timeout) or True)
    runner._flush_frame_log(1.5)
    assert calls == [1.5]


# ── WP2b-024: a len byte the app reads as negative fails the read ────────────────────────────
@pytest.mark.parametrize("stream", [
    bytes([0x01, 0x01, 0x01, 0x99, 0x80, *range(0x80)]),        # the whole-buffer parse reads it
    bytes([0x01, 0x01, 0x01, 0x04, 0x01, 0x32, 0x99, 0x90, 0x00]),   # the per-TLV walk reaches it
], ids=["whole-buffer-parse", "per-tlv-walk"])
def test_a_tlv_len_of_0x80_or_more_fails_the_read_like_the_app(stream: bytes) -> None:
    frames = _chunk(stream)
    assert r.parse_status(frames) == r.StatusReply()   # WAS: is_on / brightness applied
    _tlvs, cut = r.walk_status_tlvs(r.reassemble(frames))
    assert cut is not None and cut.reason == "unparsed" and cut.type == 0x99
    assert cut.length is not None and cut.length >= r.SIGNED_LEN_MIN
    report = status_burst_report(frames)
    assert report.malformed == 0 and report.gaps == [
        f"0xAC reply TLV type 0x99 has len 0x{cut.length:02x}: the app reads a len of 0x80 or more as "
        "negative and fails the read"]


def test_a_negative_len_with_no_value_byte_after_it_is_a_clean_tail() -> None:
    # u():271-282: when no byte follows the len, the walk stops cleanly whatever the len, before
    # `new byte[len]` could throw, and the read succeeds with what it walked.
    frames = _chunk(bytes([0x01, 0x01, 0x01, 0x04, 0x01, 0x32, 0x99, 0x90]))
    st = r.parse_status(frames)
    assert (st.is_on, st.brightness) == (True, 0x32)
    assert r.walk_status_tlvs(r.reassemble(frames))[1] == r.TlvCut("tail", 0x99, 6, 0x90)
    assert status_burst_report(frames).gaps == []


def test_a_len_below_0x80_is_still_read() -> None:
    frames = _chunk(bytes([0x01, 0x01, 0x01, 0x99, 0x7F, *range(0x7F), 0x04, 0x01, 0x32]))
    st = r.parse_status(frames)
    assert st.is_on is True and st.brightness == 0x32


# ── WP2b-008: the analyzer's residual split case ─────────────────────────────────────────────
def test_a_first_chunk_repeated_after_interior_chunks_splits_the_analyzer_burst() -> None:
    # Documented limitation (describe._split_bursts): the runtime and the app decode this read; the
    # analyzer, which has no TX boundaries, counts two incomplete bursts.
    burst = _chunk(bytes([0x01, 0x01, 0x01, 0x04, 0x01, 0x50, *([0x30, 0x02, 0x01, 0x01] * 6)]))
    assert len(burst) >= 3
    late = [burst[0], burst[1], burst[0], *burst[2:]]
    assert r.parse_status(late).is_on is True
    report = status_burst_report(late)
    assert (report.incomplete, report.malformed, dict(report.tlv_types)) == (2, 2, {})


# ── WP2b-005 / WP2b-023 / WP4-018: the analyzer CLI ──────────────────────────────────────────
def _records(frames: list[bytes], **extra: object) -> list[dict[str, object]]:
    return [{"addr": "AA:BB", "dir": "rx", "plain": fr.hex(), **extra} for fr in frames]


def test_cli_reports_a_truncated_burst(capsys: pytest.CaptureFixture[str]) -> None:
    # A complete burst whose last TLV runs past the reply: the runtime uses NO state from it, so the
    # report must not read as a clean decode.
    frames = _chunk(bytes([0x01, 0x01, 0x01, 0x04, 0x05, 0x32]))
    assert r.parse_status(frames) == r.StatusReply()
    assert debug.analyze(_records(frames)) == 0      # not a spec gap
    out = capsys.readouterr().out
    assert ("0xAC bursts whose TLV stream overruns the complete reply (walked up to the cut; the "
            "runtime uses NO state from them): 1") in out


def test_cli_reports_bursts_of_a_redacted_capture(capsys: pytest.CaptureFixture[str]) -> None:
    # A capture redacted before the masking kept the first chunk's header (every chunk ac <index>).
    zeroed = [fr[:2] + bytes(18) for fr in _chunk(bytes(range(1, 40)))]
    assert debug.analyze(_records(zeroed)) == 0
    out = capsys.readouterr().out
    assert "0xAC bursts in a redacted capture (payload zeroed; framing checked, TLVs not walked): 1" in out
    assert "malformed 0xAC bursts" not in out


def test_cli_reads_an_unknown_direction_as_unknown(capsys: pytest.CaptureFixture[str]) -> None:
    # A capture's free-form "dir" is not passed on: "RX" is neither rx nor tx, so the 0x33 frame is
    # read as a write command, not as a rejected write-ACK.
    rejected_ack = frame(0x33, 0x01, 0x05)
    assert debug.analyze(_records([rejected_ack], dir="RX")) == 0
    assert "?  write/switch" in capsys.readouterr().out
    assert debug.analyze(_records([rejected_ack])) == 1   # a real rx: the device rejected the write


def test_frames_log_strips_the_default_logging_prefix_from_the_address() -> None:
    line = "DEBUG:govee_ble_local.frames:AA:BB:CC:DD:EE:FF tx power plain=00 wire=00 enc=none"
    assert debug.frames_log_to_records(line) == [
        {"addr": "AA:BB:CC:DD:EE:FF", "dir": "tx", "plain": "00", "wire": "00", "enc": "none"}]
    ha = ("2026-10-15 10:00:00 DEBUG (MainThread) [govee_ble_local.frames] "
          "AA:BB:CC:DD:EE:FF rx cipher plain= wire=01 enc=e7")
    assert debug.frames_log_to_records(ha)[0]["addr"] == "AA:BB:CC:DD:EE:FF"


# ── WP2b-023: the masking keeps the first chunk's header; a chunk lost before 0xFF shows ───────
@pytest.fixture
def masking(monkeypatch: pytest.MonkeyPatch) -> Iterator[ModuleType]:
    monkeypatch.syspath_prepend(str(_TOOLS))
    yield importlib.import_module("field_test.masking")


def test_redacted_capture_still_shows_a_chunk_lost_before_the_terminator(
    masking: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    burst = _chunk(bytes(range(1, 60)))           # 4 packets: 00, 01, 02, ff
    assert [fr[1] for fr in burst] == [0x00, 0x01, 0x02, 0xFF]
    path = tmp_path / "h60a6-frames-T.jsonl"
    rows = [{"ts": i, "addr": ADDR, "dir": "rx", "plain": fr.hex(), "wire": None}
            for i, fr in enumerate([*burst[:2], burst[3]])]   # chunk 0x02 lost, just before 0xFF
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    masking.redact_frames(path, ADDR)
    records = [json.loads(line) for line in path.read_text().splitlines()]
    assert str(records[0]["plain"]).startswith(burst[0][:7].hex())   # header kept, data zeroed
    assert str(records[0]["plain"])[14:] == "0" * 26
    assert debug.analyze(records) == 0
    out = capsys.readouterr().out
    assert "malformed 0xAC bursts skipped (dropped/merged frames, NOT spec gaps): 1" in out


def test_redaction_still_zeroes_every_status_data_byte(masking: ModuleType, tmp_path: Path) -> None:
    burst = _chunk(bytes([0x07, 0x11, 0x10, *b"SERIAL-0123456789"]))
    path = tmp_path / "f.jsonl"
    path.write_text("".join(json.dumps({"dir": "rx", "plain": fr.hex()}) + "\n" for fr in burst))
    masking.redact_frames(path, None)
    text = path.read_text()
    assert b"SERIAL".hex() not in text and b"0123".hex() not in text


def test_every_read_request_names_the_reply_it_waits_for() -> None:
    # The reply key is the built request's own read_command.command (AbsControllerNoEvent4Single.
    # isSameController matches proType and command only), the byte the hand-paired build.CMD_* held.
    pairs = [
        (build.bulb_group_query(1, v2=True), build.CMD_BULB_COLOR_V2),
        (build.ic_count_query(), build.CMD_IC_NUM),
        (build.soft_version_query(), build.CMD_SOFT_VERSION),
        (build.device_info_query(build.INFO_SN), build.CMD_DEVICE_INFO),
        (build.bar_switch_query(), build.CMD_BAR_SWITCH), (build.gradual_query(), build.CMD_GRADUAL_WIFI_BLE),
        (build.power_query(), build.CMD_SWITCH), (build.brightness_query(), build.CMD_BRIGHTNESS),
        (build.mode_query(), build.CMD_MODE), (build.section_count_query(), build.CMD_SECTION_COUNT),
        (build.secret_read(), build.CMD_SECRET_READ),
    ]
    assert [parse.request_command(f) for f, _ in pairs] == [k for _, k in pairs]
    with pytest.raises(ValueError, match="not an aa read request"):
        parse.request_command(build.switch(True))
