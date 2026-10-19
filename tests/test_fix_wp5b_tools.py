"""Regression tests for the developer-tool fixes (decrypt_btsnoop, fetch_scenes,
power_semantics_probe, h60a6_live_check). Offline: no BLE hardware, no network."""
from __future__ import annotations

import asyncio
import importlib.util
import inspect
import re
import struct
import subprocess
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from govee_ble_local.const import PSK
from govee_ble_local.crypto import checksum, checksum_ok, decrypt, encrypt
from govee_ble_local.models import Segment
from govee_ble_local import profile_for
from govee_ble_local.transport import handshake
from govee_ble_local.wire import build

TOOLS = Path(__file__).resolve().parent.parent / "tools"
KEY = bytes(range(0x40, 0x50))  # a session key (any 16 bytes)


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"_tool_wp5b_{name}", TOOLS / f"{name}.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _now(_delay: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", _now)


# --- decrypt_btsnoop ------------------------------------------------------------------------


def _with_bcc(body: bytes) -> bytes:
    return body + bytes([checksum(body)])


def _key_reply(key: bytes) -> bytes:
    """The device's PSK-encrypted e7 01 <key> handshake reply."""
    return encrypt(_with_bcc(bytes([0xE7, 0x01]) + key + bytes(1)), PSK)


def _pdu(peer: str, direction: str, value: bytes, handle: int = 1) -> tuple[int, str, int, bytes]:
    op = b"\x52\x00\x00" if direction == "TX" else b"\x1b\x00\x00"
    return handle, peer, 0, op + value


def _run_main(mod: ModuleType, monkeypatch: pytest.MonkeyPatch, pdus: list[tuple[int, str, int, bytes]],
              capsys: pytest.CaptureFixture[str]) -> str:
    monkeypatch.setattr(mod, "_att_pdus", lambda path: iter(pdus))
    mod.main("x", set(), True, 1000, False)
    return capsys.readouterr().out


@pytest.mark.parametrize("mtu", [20, 64])
def test_decrypt_accepts_exact_length_a4_frames(mtu: int) -> None:
    mod = _load("decrypt_btsnoop")
    frames = build.scene_upload_a4_mtu(bytes(range(150)), 0x58, mtu=mtu)
    assert any(len(f) != 20 for f in frames)        # short END (and MTU-sized frames at mtu=64)
    for f in frames:
        assert mod._try_plain(encrypt(f, KEY), KEY) == (f, "enc")
        assert mod._try_plain(f, None) == (f, "plain")
    end_only = _with_bcc(bytes([0xA4, 0xFF, 0xFF]))  # 4-byte terminator
    a3_end = _with_bcc(bytes([0xA3, 0xFF]))          # 3-byte 0xA3 terminator (packageSize variant)
    assert mod._try_plain(end_only, None) == (end_only, "plain")
    assert mod._try_plain(a3_end, None) == (a3_end, "plain")


def _a6_frames(value: bytes, mtu: int) -> list[bytes]:
    """One 0xA6 group as MultipleControllerCommV1.makeSendBytesMtu0xA6 frames it: exact-length with
    the BCC appended. An MTU-sized START (data @17) or, when the value fits, a short START plus the
    4-byte [A6 FF FF BCC] end; then MTU-sized MIDDLEs [A6 lo hi data] and a short [A6 FF FF data] END."""
    first, step = mtu - 18, mtu - 4
    head = bytes([0xA6, 0x00, 0x00, 0x01, 1, 1]) + bytes(8)        # groups, group no., 8 verify bytes
    if len(value) <= first:
        return [_with_bcc(head + (2).to_bytes(2, "little") + b"\x63" + value), _with_bcc(b"\xa6\xff\xff")]
    rest = value[first:]
    chunks = [rest[i : i + step] for i in range(0, len(rest), step)]
    frames = [_with_bcc(head + (1 + len(chunks)).to_bytes(2, "little") + b"\x63" + value[:first])]
    frames += [_with_bcc(b"\xa6" + i.to_bytes(2, "little") + c) for i, c in enumerate(chunks[:-1], start=1)]
    return [*frames, _with_bcc(b"\xa6\xff\xff" + chunks[-1])]


@pytest.mark.parametrize(("size", "mtu"), [(10, 64), (150, 64), (150, 20)])
def test_decrypt_accepts_exact_length_a6_frames(size: int, mtu: int) -> None:
    mod = _load("decrypt_btsnoop")
    frames = _a6_frames(bytes(range(size)), mtu)
    assert any(len(f) != 20 for f in frames)        # short START/END (and MTU-sized frames at mtu=64)
    for f in frames:
        assert mod._try_plain(encrypt(f, KEY), KEY) == (f, "enc")
        assert mod._try_plain(f, None) == (f, "plain")


def test_decrypt_dumps_a6_starts_by_default(monkeypatch: pytest.MonkeyPatch,
                                            capsys: pytest.CaptureFixture[str]) -> None:
    mod = _load("decrypt_btsnoop")
    dump = mod.parse_args(["x.log"]).dump
    assert dump == {0xA1, 0xA3, 0xA4, 0xA6}
    frames = _a6_frames(bytes(range(150)), 64)
    pdus = [_pdu("AA", "TX", handshake.build_step1(PSK)), _pdu("AA", "RX", _key_reply(KEY))]
    pdus += [_pdu("AA", "TX", encrypt(f, KEY)) for f in frames]
    monkeypatch.setattr(mod, "_att_pdus", lambda path: iter(pdus))
    mod.main("x", dump, False, 1000, True)          # default dump set, --starts
    out = capsys.readouterr().out
    assert f"enc    64B  {frames[0].hex()}" in out
    assert not any(f.hex() in out for f in frames[1:])


def test_decrypt_exact_length_needs_a_multi_packet_opcode() -> None:
    mod = _load("decrypt_btsnoop")
    for odd in (_with_bcc(bytes([0x33, 0x01, 0x01])), _with_bcc(bytes([0xAA]) + bytes(30)), b"\xa4\xa4"):
        assert mod._try_plain(odd, None) is None


def test_decrypt_prints_mtu_sized_writes(monkeypatch: pytest.MonkeyPatch,
                                         capsys: pytest.CaptureFixture[str]) -> None:
    mod = _load("decrypt_btsnoop")
    frames = build.scene_upload_a4_mtu(bytes(range(150)), 0x58, mtu=64)
    pdus = [_pdu("AA", "TX", handshake.build_step1(PSK)), _pdu("AA", "RX", _key_reply(KEY))]
    pdus += [_pdu("AA", "TX", encrypt(f, KEY)) for f in frames]
    out = _run_main(mod, monkeypatch, pdus, capsys)
    assert "handshakes (session keys derived): 1" in out
    for f in frames:
        assert f" {len(f):3d}B  {f.hex()}" in out


def test_decrypt_takes_a_key_only_in_reply_to_a_request(monkeypatch: pytest.MonkeyPatch,
                                                        capsys: pytest.CaptureFixture[str]) -> None:
    mod = _load("decrypt_btsnoop")
    frame = _with_bcc(bytes([0x33, 0x01, 0x01]) + bytes(16))
    request = _pdu("AA", "TX", handshake.build_step1(PSK))
    reply, write = _pdu("AA", "RX", _key_reply(KEY)), _pdu("AA", "TX", encrypt(frame, KEY))
    # a notify that PSK-decrypts to e7 01 with no e7 01 request before it is not a key
    out = _run_main(mod, monkeypatch, [reply, write], capsys)
    assert "handshakes (session keys derived): 0" in out and frame.hex() not in out
    out = _run_main(mod, monkeypatch, [request, reply, write], capsys)
    assert "handshakes (session keys derived): 1" in out and f"enc    20B  {frame.hex()}" in out


def _plain_frame_passing_bcc_under(key: bytes) -> bytes:
    """A BCC-valid plaintext frame whose decrypt under ``key`` is BCC-valid too (~1 in 256)."""
    for i in range(1 << 16):
        f = _with_bcc(bytes([0x33, 0x05, i & 0xFF, i >> 8]) + bytes(15))
        if checksum_ok(decrypt(f, key)):
            return f
    raise AssertionError("no candidate frame")  # pragma: no cover


def test_decrypt_session_key_does_not_leak_to_a_reused_handle(monkeypatch: pytest.MonkeyPatch,
                                                              capsys: pytest.CaptureFixture[str]) -> None:
    mod = _load("decrypt_btsnoop")
    plain = _plain_frame_passing_bcc_under(KEY)
    pdus = [_pdu("AA", "TX", handshake.build_step1(PSK)), _pdu("AA", "RX", _key_reply(KEY)),
            _pdu("BB", "TX", plain)]  # a plaintext device on the same ACL handle, later
    out = _run_main(mod, monkeypatch, pdus, capsys)
    assert f"BB h0x001 TX plain  20B  {plain.hex()}" in out


def _record(data: bytes) -> bytes:
    return struct.pack(">IIIIq", len(data), len(data), 0, 0, 0) + data


def test_decrypt_reassembles_l2cap_fragments_with_peer(tmp_path: Path) -> None:
    mod = _load("decrypt_btsnoop")
    handle = 0x0042
    peer = bytes([0xFF, 0xEE, 0xDD, 0xCC, 0xBB, 0xAA])     # little-endian on the wire
    params = bytes([0x00]) + struct.pack("<H", handle) + bytes([0x00, 0x00]) + peer + bytes(7)
    conn_complete = bytes([0x04, 0x3E, 1 + len(params), 0x01]) + params
    att = b"\x52\x15\x00" + bytes(range(64))                # an MTU-sized ATT write
    l2 = struct.pack("<HH", len(att), 0x0004) + att
    first, rest = l2[:27], l2[27:]
    acl_start = b"\x02" + struct.pack("<HH", handle | (0b10 << 12), len(first)) + first
    acl_cont = b"\x02" + struct.pack("<HH", handle | (0b01 << 12), len(rest)) + rest
    cap = tmp_path / "cap.log"
    cap.write_bytes(b"btsnoop\x00" + struct.pack(">II", 1, 1002)
                    + _record(conn_complete) + _record(acl_start) + _record(acl_cont))
    assert list(mod._att_pdus(str(cap))) == [(handle, "AA:BB:CC:DD:EE:FF", 0, att)]


# --- fetch_scenes ---------------------------------------------------------------------------


def test_merge_takes_fresh_blob_and_drops_stale_routing_fields() -> None:
    fs = _load("fetch_scenes")
    existing = {"A": {"code": 10, "param": "AAAA", "scene_type": 1, "big_effect": True}}
    fresh = {"A": {"code": 11, "param": "BBBB", "category": None}}
    assert fs._merge(existing, fresh) == {"A": {"code": 11, "param": "BBBB", "category": None}}


def test_merge_keeps_a_resolved_blob_over_the_public_stub() -> None:
    fs = _load("fetch_scenes")
    existing = {"A": {"code": 10, "param": "REAL", "param_id": 5, "scene_type": 1, "big_effect": True,
                      "category": "old"},
                "Gone": {"code": 3, "param": None}}
    stub = {"code": 12, "param": "STUB", "param_id": 5, "placeholder": True, "scene_type": 2,
            "category": "new"}
    merged = fs._merge(existing, {"A": stub})
    assert merged["A"] == {"code": 12, "param": "REAL", "param_id": 5, "scene_type": 2, "category": "new"}
    assert merged["Gone"] == existing["Gone"]                   # no longer public: kept
    other = dict(stub, param_id=6)                              # a different effect: re-resolve it
    assert fs._merge(existing, {"A": other})["A"] == other


_ROW = re.compile(r"^(\S+)\s+(\d+)\s+(\d+)(\[[^\]]*\])?\s+(\d+)\s+(\d+)\s+(\d+)\s+(\[.*\])$")


def _audit_rows(out: str) -> list[re.Match[str]]:
    rows = [_ROW.match(line) for line in out.splitlines()[1:]]
    assert all(rows), out
    return [r for r in rows if r]


def test_audit_reports_versions_and_one_bucket_per_scene(capsys: pytest.CaptureFixture[str]) -> None:
    fs = _load("fetch_scenes")
    fs.audit(["H60A6"])
    (row,) = _audit_rows(capsys.readouterr().out)
    total, upload, activate, blocked, placeh = (int(row.group(i)) for i in (2, 3, 5, 6, 7))
    assert row.group(1) == "H60A6"
    assert upload + activate + blocked + placeh == total
    profile = profile_for("H60A6")
    assert profile is not None and row.group(8) == str(sorted(profile.scene_versions)) != "[]"


def test_audit_counts_planner_refusals_and_propagates_bugs(monkeypatch: pytest.MonkeyPatch,
                                                           capsys: pytest.CaptureFixture[str]) -> None:
    from govee_ble_local.devices.device import Device
    from govee_ble_local.exceptions import GoveeBleInvalidValue

    fs = _load("fetch_scenes")

    def refuse(self: Device, scene: Any) -> None:
        raise GoveeBleInvalidValue("malformed")

    monkeypatch.setattr(Device, "scene_upload_frames", refuse)
    fs.audit(["H60A6"])
    (row,) = _audit_rows(capsys.readouterr().out)
    assert int(row.group(6)) == int(row.group(2)) - int(row.group(7))   # every non-placeholder BLOCKED

    def bug(self: Device, scene: Any) -> None:
        raise TypeError("programming error")

    monkeypatch.setattr(Device, "scene_upload_frames", bug)
    with pytest.raises(TypeError):
        fs.audit(["H60A6"])


def test_audit_reports_a_catalogued_sku_the_table_does_not_support(monkeypatch: pytest.MonkeyPatch,
                                                                  capsys: pytest.CaptureFixture[str]) -> None:
    from govee_ble_local.scenes import Scene

    fs = _load("fetch_scenes")
    monkeypatch.setattr("govee_ble_local.scenes.load_scenes", lambda sku: {"a": Scene("a", 1, None)})
    fs.audit(["H9999"])
    assert "H9999   (not a supported SKU)" in capsys.readouterr().out


def test_audit_cli_honours_positional_skus() -> None:
    r = subprocess.run(  # noqa: S603 - fixed argv, sys.executable
        [sys.executable, str(TOOLS / "fetch_scenes.py"), "--audit", "H60A6"],
        capture_output=True, text=True, timeout=120,
    )
    assert r.returncode == 0, r.stderr
    assert [row.group(1) for row in _audit_rows(r.stdout)] == ["H60A6"]


def test_main_counts_corrupt_catalog_and_write_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
                                                       capsys: pytest.CaptureFixture[str]) -> None:
    fs = _load("fetch_scenes")
    monkeypatch.setattr(fs, "OUT_DIR", tmp_path)
    monkeypatch.setattr(fs, "fetch", lambda sku, app_version="": {"A": {"code": 1, "param": None}})
    (tmp_path / "index.json").write_text('{"H60A6": "0123456789abcdef"}', encoding="utf-8")
    (tmp_path / "0123456789abcdef.json").write_text('{"A": "not a scene object"}', encoding="utf-8")
    assert fs.main(["H60A6", "H61A8"]) == 1            # the corrupt one fails, the run goes on
    assert "H60A6: FETCH FAILED" in capsys.readouterr().err
    assert fs.read_catalog("H61A8", tmp_path) == {"A": {"code": 1, "param": None}}

    def disk_full(path: Path, data: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(fs, "write_catalog", disk_full)
    assert fs.main(["H61A8", "H6052"]) == 2
    assert capsys.readouterr().err.count("WRITE FAILED") == 2


# --- power_semantics_probe -------------------------------------------------------------------


def _profile(sku: str) -> Any:
    profile = profile_for(sku)
    assert profile is not None
    return profile


def test_power_probe_zone_frames_follow_the_profile() -> None:
    pp = _load("power_semantics_probe")
    bars = _profile("H6047")
    assert pp.zone_frames(bars, {0: True, 1: False}) == [build.bar_switch(True, False)]
    assert pp.zone_frames(bars, {0: True}) == []      # never switch an unknown bar blindly
    assert pp.zone_frames(_profile("H60A6"), {0: True, 1: False}) == [
        build.zone_power(0, True), build.zone_power(1, False)]


def test_power_probe_restore_plan() -> None:
    pp = _load("power_semantics_probe")
    # bar light: 33 01 first (it doesn't say which bars light), then the exact 33 36 bar frame
    assert pp.restore_plan(_profile("H6047"), True, {0: False, 1: True}) == (
        [("global", [build.switch(True)]), ("zone", [build.bar_switch(False, True)])], True)
    # zoned light: zones first, the global switch last
    assert pp.restore_plan(_profile("H60A6"), False, {0: True, 1: False}) == (
        [("zone", [build.zone_power(0, True), build.zone_power(1, False)]),
         ("global", [build.switch(False)])], True)
    assert pp.restore_plan(_profile("H6047"), True, {1: True}) == ([("global", [build.switch(True)])], False)
    # neither bar read back (aa 36 gave no reply): no bar frame, and NOT complete
    assert pp.restore_plan(_profile("H6047"), True, {}) == ([("global", [build.switch(True)])], False)
    # a zoned light with a zone (or the power state) unknown restores what it knows, but isn't complete
    assert pp.restore_plan(_profile("H60A6"), False, {}) == ([("global", [build.switch(False)])], False)
    assert pp.restore_plan(_profile("H60A6"), False, {0: True}) == (
        [("zone", [build.zone_power(0, True)]), ("global", [build.switch(False)])], False)
    assert pp.restore_plan(_profile("H60A6"), None, {0: True, 1: False}) == (
        [("zone", [build.zone_power(0, True), build.zone_power(1, False)])], False)
    assert pp.unknown_zones(_profile("H6047"), {1: True}) == [0]


class _ProbeConn:
    def __init__(self, nak_cmd: int | None) -> None:
        self.sent: list[bytes] = []
        self.nak_cmd = nak_cmd

    async def send(self, frame: bytes) -> bytes:
        self.sent.append(frame)
        if frame[1] == self.nak_cmd:
            raise RuntimeError("no ack")
        return bytes([0x33, frame[1], 0x00]) + bytes(17)


def _run_probe(monkeypatch: pytest.MonkeyPatch, sku: str, nak_cmd: int | None,
               start: SimpleNamespace) -> _ProbeConn:
    pp = _load("power_semantics_probe")
    conn = _ProbeConn(nak_cmd)

    async def _noop() -> None:
        return None

    async def _update() -> SimpleNamespace:
        return start

    dev = SimpleNamespace(profile=_profile(sku), _conn=conn, state=start, update=_update,
                          connect=_noop, stop=_noop)

    class _Scanner:
        @staticmethod
        async def find_device_by_address(address: str, timeout: float) -> object:
            return object()

    monkeypatch.setattr(pp, "BleakScanner", _Scanner)
    monkeypatch.setattr(pp, "create_device", lambda ble, sku, idle_disconnect=None: dev)
    asyncio.run(pp.main("AA:BB:CC:DD:EE:FF", sku))
    return conn


def test_power_probe_restores_bars_with_the_bar_frame(monkeypatch: pytest.MonkeyPatch, no_sleep: None,
                                                      capsys: pytest.CaptureFixture[str]) -> None:
    start = SimpleNamespace(is_on=True, zone_power={0: False, 1: True}, brightness=50)
    conn = _run_probe(monkeypatch, "H6047", None, start)
    assert conn.sent[-2:] == [build.switch(True), build.bar_switch(False, True)]
    assert all(f[1] != 0x30 for f in conn.sent)       # H60A6's 33 30 is never sent to the bar light
    out = capsys.readouterr().out
    assert "restored" in out and "RESTORE FAILED" not in out


def test_power_probe_reports_a_failed_restore_frame(monkeypatch: pytest.MonkeyPatch, no_sleep: None,
                                                    capsys: pytest.CaptureFixture[str]) -> None:
    start = SimpleNamespace(is_on=True, zone_power={0: False, 1: True}, brightness=50)
    _run_probe(monkeypatch, "H6047", 0x36, start)     # every bar frame un-acked
    assert "RESTORE FAILED" in capsys.readouterr().out


def test_power_probe_unknown_bars_fail_the_restore(monkeypatch: pytest.MonkeyPatch, no_sleep: None,
                                                   capsys: pytest.CaptureFixture[str]) -> None:
    start = SimpleNamespace(is_on=True, zone_power={}, brightness=50)   # the aa 36 read gave nothing
    conn = _run_probe(monkeypatch, "H6047", None, start)
    assert build.bar_switch(False, False) in conn.sent                # the experiment did switch the bars
    assert conn.sent[-1] == build.switch(True)                        # ...the restore can't put them back
    out = capsys.readouterr().out
    assert "starting bar state unknown (power index [0, 1])" in out
    assert "RESTORE FAILED" in out and "-> restored" not in out


def test_power_probe_restores_zones_then_global(monkeypatch: pytest.MonkeyPatch, no_sleep: None,
                                                capsys: pytest.CaptureFixture[str]) -> None:
    start = SimpleNamespace(is_on=False, zone_power={0: True, 1: False}, brightness=50)
    conn = _run_probe(monkeypatch, "H60A6", None, start)
    assert conn.sent[-3:] == [build.zone_power(0, True), build.zone_power(1, False), build.switch(False)]
    assert "RESTORE FAILED" not in capsys.readouterr().out


# --- h60a6_live_check -------------------------------------------------------------------------


def test_live_check_segments_match_by_segment_index(no_sleep: None) -> None:
    lc = _load("h60a6_live_check")
    # read back out of list order: the check must key on Segment.index, not list position
    segs = [Segment(2, (0, 0, 255)), Segment(0, (255, 0, 0)), Segment(1, (0, 255, 0))]

    async def _noop(*_a: object) -> None:
        return None

    async def _update() -> SimpleNamespace:
        return dev.state

    dev = SimpleNamespace(profile=SimpleNamespace(segments=3), state=SimpleNamespace(segments=segs),
                          set_brightness=_noop, set_segment_rgb=_noop, update=_update)
    ok, detail = asyncio.run(lc.Runner(dev).segments())
    assert ok, detail


# --- private library names the tools reach into (no semver promise: fail loudly here) ----------


def test_private_library_names_the_tools_use_still_exist() -> None:
    from fake_ble import ble_device

    from govee_ble_local.registry import create_device

    dev = create_device(ble_device("AA:BB:CC:DD:EE:FF", "H60A6"), "H60A6")
    # power_semantics_probe (raw send) and h60a6_live_check.read_kelvin (raw query)
    assert inspect.iscoroutinefunction(dev._conn.send) and inspect.iscoroutinefunction(dev._conn.query)
    # fetch_scenes --audit
    assert isinstance(dev.profile.scene_versions, frozenset)
