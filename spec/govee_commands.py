#!/usr/bin/env python3
"""Govee BLE command builders — the full set of 0x33 write and 0xAA read frames the Govee Home app
emits, one function per (opcode, variant), traced to the decompiled app (controller cited per function)
and verified by adversarial re-trace. Companion to govee_reference.py, which owns the framing primitives
(BCC, cipher, reassembly, parse dispatch). A builder returns a 20-byte frame, or a list of frames for a
multi-packet scene/DIY upload (send them in order). Tiers and gaps: docs/OPCODE_COVERAGE.md.

Assembled from the opcode trace, then maintained in place (ruff- and mypy --strict-clean): edit this file
directly, keeping the 759 _SELF_CHECKS green.
"""

from __future__ import annotations

import struct
from collections.abc import Callable, Sequence
from typing import TypeVar, cast

from govee_reference import OP15_MAX_MASK_BYTES, append_bcc, bcc, op15_mask

_T = TypeVar("_T")
_K = TypeVar("_K")
_V = TypeVar("_V")


def _f32(x: float) -> float:
    """Round to IEEE-754 single precision (a Java ``float``), as NumberUtil's float divisor 100 forces."""
    return float(struct.unpack("<f", struct.pack("<f", x))[0])


def read_request(command: int, *selector: int) -> bytes:
    """0xAA read request: [0xAA, command, *selector], zero-padded, BCC at byte 19."""
    return append_bcc(bytes([0xAA, command & 0xFF, *[s & 0xFF for s in selector]]))


def read_connect_heartbeat(heartbeat_type: int) -> bytes:
    """Heartbeat request for a ConnectHeartbeat type: 1->AA 01, 3->AA 33, 4->AA 36, else AA 00
    (base-classes12/com/govee/common2light/capability/connect/ConnectHeartbeat.java:19-38 b()/c()).
    """
    return read_request({1: 0x01, 3: 0x33, 4: 0x36}.get(heartbeat_type, 0x00))


def read_heartbeat() -> bytes:
    """0xAA 00 keep-alive, reply ignored (HeartControllerNoEvent.getCommandType,
    base-classes10/com/govee/base2light/ble/controller/HeartControllerNoEvent.java:10-12; HeartFactory type
    2 base-classes10/com/govee/base2light/connect/factory/HeartFactory.java:23-26).
    """
    return read_request(0x00)


def build_h5086_chart_time_range(start_unix: int, end_unix: int) -> bytes:
    """0x33 01 [start u32be][end u32be]: H5086 chart-history time range, written to service
    494e5445-4c4c-495f-524f-434b535f4857 / char ...2014 (ChartTimeRangeController.q :52-56, e()/d()
    :33-40).
    """
    return append_bcc(
        bytes([0x33, 0x01])
        + (start_unix & 0xFFFFFFFF).to_bytes(4, "big")
        + (end_unix & 0xFFFFFFFF).to_bytes(4, "big")
    )


def build_music_old_0x01(code: int, sensitivity: int, rgb: Sequence[int] = (0xFF, 0x00, 0x00)) -> bytes:
    """0x33 05 01 [code, sensitivity, (R,G,B only when code is 1 or 2)] old-music write
    (SubModeMusic.g/getWriteBytes, split_rgblight/com/govee/h6104/ble/SubModeMusic.java:42-45,51-63,87-88;
    report parser split_rgblight/com/govee/h6104/ble/Mode.java:8-12 -> SubModeMusic.parse :75-80).
    """
    body = [0x33, 0x05, 0x01, code & 0xFF, sensitivity & 0xFF]
    if code in (1, 2):
        body += [c & 0xFF for c in rgb]
    return append_bcc(bytes(body))


def build_plug_relay(outlet: int, on: bool) -> bytes:
    """0x33 01: h5080 plug relay mask, outlet 0/1/2 or any other value for all (SwitchControllerV2.q,
    split_plugnew/com/govee/h5080/ble/controller/SwitchControllerV2.java:53-71).
    """
    if outlet == 0:
        v = 0x10 | (0x01 if on else 0x00)
    elif outlet == 1:
        v = 0x20 | (0x02 if on else 0x00)
    elif outlet == 2:
        v = 0x40 | (0x04 if on else 0x00)
    else:
        v = 0xF0 | (0x0F if on else 0x00)
    return append_bcc(bytes([0x33, 0x01, v]))


def build_plug_relay_with_secret(outlet: int, on: bool, secret: bytes) -> list[bytes]:
    """[0x33 B2 secret, 0x33 01 mask]: h5080 plug switch preceded by the 8-byte secret frame (h5080 Model.l
    :150 with outlet 15; WidgetBleIotOpSubV2.sendCmd :104-107).
    """
    if len(secret) != 8:
        raise ValueError("secret must be 8 bytes")
    if outlet == 0:
        v = 0x10 | (0x01 if on else 0x00)
    elif outlet == 1:
        v = 0x20 | (0x02 if on else 0x00)
    elif outlet == 2:
        v = 0x40 | (0x04 if on else 0x00)
    else:
        v = 0xF0 | (0x0F if on else 0x00)
    return [append_bcc(bytes([0x33, 0xB2]) + bytes(secret)), append_bcc(bytes([0x33, 0x01, v]))]


def build_h5086_chart_prepare() -> bytes:
    """0x33 02 (no payload) H5086 chart prepare on the 494e5445-...-4857 service (ChartPrepareInfoController,
    split_plugnew/com/govee/h5086/ble/chartcontroller/ChartPrepareInfoController.java:28-53; caller
    split_plugnew/com/govee/h5086/ble/AbsBleWithChartDataComm.java:202).
    """
    return append_bcc(bytes([0x33, 0x02]))


def build_h5089_total_switch(on: bool) -> bytes:
    """33 02 on - H5089 (goodsType 307) all-outlet switch (Controller4TotalSwitch.q
    split_plugnew/com/govee/h5080/ble/controller/h5089/Controller4TotalSwitch.java:14-35;
    SwitchControllerConfig.d
    base-classes11/com/govee/base2light/pact/newdetail/config/fuc/SwitchControllerConfig.java:112).
    """
    return append_bcc(bytes([0x33, 0x02, 0x01 if on else 0x00]))


def build_plug_total_switch(on: bool) -> bytes:
    """0x33 02 [on] H5089 total switch (Controller4TotalSwitch.q,
    split_plugnew/com/govee/h5080/ble/controller/h5089/Controller4TotalSwitch.java:14-35, caller
    split_plugnew/com/govee/h5080/adjust/h5089/Ui4H5089.java:946; goodsType 307 in SwitchControllerConfig
    base-classes11/com/govee/base2light/pact/newdetail/config/fuc/SwitchControllerConfig.java:108-121,130-138).
    """
    return append_bcc(bytes([0x33, 0x02, 0x01 if on else 0x00]))


def build_music_flag_sens_03(sensitivity: int, rgb: Sequence[int] | None = None) -> bytes:
    """0x33 05 03 [colour_flag (0 auto / 1 rgb), sensitivity, (R,G,B) only when flag=1] old RGB music
    (SubModeMusic.a/getWriteBytes, split_rgblight/com/govee/h6160/ble/SubModeMusic.java:18-20,54-60,89-91;
    report parser split_rgblight/com/govee/h6160/ble/Mode.java:15-19 -> SubModeMusic.parse :76-83).
    """
    body = [0x33, 0x05, 0x03, 0 if rgb is None else 1, sensitivity & 0xFF]
    if rgb is not None:
        body += [c & 0xFF for c in rgb]
    return append_bcc(bytes(body))


def build_brightness_from_percent(pct: int, lo: int = 20, hi: int = 254) -> bytes:
    """33 04 [level] with a 1-100 percent mapped into the [lo, hi] range, group/apply path only
    (BrightnessConfig.Companion.h/c
    base-classes11/com/govee/base2light/pact/newdetail/config/fuc/BrightnessConfig.java:88-93,153-155;
    NumberUtil.calculateProgress base-classes10/com/govee/base2home/util/NumberUtil.java:47-49; caller
    GroupApplyVM base-classes13/com/govee/eco/basegroup/viewmodel/GroupApplyVM.java:2146-2147).
    The mapping is computed in float32, as the app's float divisor 100 forces (see :func:`_f32`).
    """
    p = min(max(pct, 1), 100)
    if lo == 1 and hi == 100:
        level = p
    else:
        step = _f32(_f32((hi - lo) + 3) / _f32(100.0))  # ((max - min) + 3) / 100, in float32
        level = int(_f32(lo + _f32((p - 1) * step)))
    return append_bcc(bytes([0x33, 0x04, level & 0xFF]))


def read_brightness() -> bytes:
    """Read brightness, aa 04 with no selector (ControllerBrightness.Companion.b
    base-classes11/com/govee/base2light/kt/general_controller/ControllerBrightness.java:40-48;
    BrightnessController()
    base-classes10/com/govee/base2light/ble/controller/BrightnessController.java:10-12).
    """
    return read_request(0x04)


def build_color_0b_kelvin(r: int, g: int, b: int, kelvin: int, seg_mask: int) -> bytes:
    """33 05 0B [R,G,B][kelvin u16be][seg_mask, positions 0-14, u16le] (colour version 6: Config.eu
    base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Config.java:13930-13934 -> Bytes.c
    base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Bytes.java:239-267).
    """
    return append_bcc(
        bytes(
            [
                0x33,
                0x05,
                0x0B,
                r,
                g,
                b,
                (kelvin >> 8) & 0xFF,
                kelvin & 0xFF,
                seg_mask & 0xFF,
                (seg_mask >> 8) & 0x7F,
            ]
        )
    )


def build_color_0b_white_tab(tint_r: int, tint_g: int, tint_b: int, pieces: int) -> bytes:
    """33 05 0B [tint RGB][mask]: white-light-tab Kelvin, colour version 3; the mask is all n pieces for 1..16
    and 00 00 above 16 because only makeSelectedTwoBytes is called (Bytes.z
    base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Bytes.java:609-621 via Config.HM
    base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Config.java:10146-10148; BleUtils.Y
    base-classes10/com/govee/base2kt/utils/BleUtils.java:444-449).
    """
    mask = (1 << pieces) - 1 if 0 < pieces <= 16 else 0
    return append_bcc(bytes([0x33, 0x05, 0x0B, tint_r, tint_g, tint_b, mask & 0xFF, (mask >> 8) & 0xFF]))


def build_color_cct_0d(
    r: int, g: int, b: int, kelvin: int = 0, tint: tuple[int, int, int] = (0, 0, 0)
) -> bytes:
    """33 05 0D [R,G,B][kelvin u16be][tint RGB]: RGB = Bytes.f, colour-tab CCT = Bytes.p, white-light-tab CCT
    = Bytes.A
    (base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Bytes.java:291-299,431-450,54-73
    via Config.bu/GM
    base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Config.java:13459-13467,9972-9974;
    rgblight VM4Light.configKelvinController split_rgblight/com/govee/rgblight/VM4Light.java:1024-1047;
    legacy split_bulblightstringv1/com/govee/bulblightv3/ble/SubModeColor.java:62-72).
    """
    return append_bcc(bytes([0x33, 0x05, 0x0D, r, g, b, (kelvin >> 8) & 0xFF, kelvin & 0xFF, *tint]))


def build_color_legacy(
    r: int, g: int, b: int, white_flag: int = 0, r2: int = 0, g2: int = 0, b2: int = 0
) -> bytes:
    """33 05 02 [R,G,B,white_flag,R2,G2,B2]: RGB = Bytes.a, colour-tab CCT = Bytes.o(FFFFFF, tint),
    white-light-tab CCT = Bytes.y
    (base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Bytes.java:214-222,423-428,589-606
    via Config.fu/IM
    base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Config.java:14090-14094,10388-10390;
    legacy SubModeColor.getWriteBytes split_rgblight/com/govee/h6159/ble/SubModeColor.java:58-66).
    """
    return append_bcc(bytes([0x33, 0x05, 0x02, r, g, b, white_flag, r2, g2, b2]))


def build_color_rgbic_0b(r: int, g: int, b: int, seg_mask: int, *, mask_bytes: int = 2) -> bytes:
    """33 05 0B [R,G,B][seg_mask LSB-first: u16le up to 16 positions, ceil(n/8) bytes capped at 13 above]
    (Bytes.d base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Bytes.java:270-281 via
    Config.cu base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Config.java:13616-13620;
    BleUtils.X/Y base-classes10/com/govee/base2kt/utils/BleUtils.java:406-490; legacy
    split_rgbiclight/com/govee/dreamcolorlightv1/ble/SubModeColor.java:198-221).
    """
    return append_bcc(bytes([0x33, 0x05, 0x0B, r, g, b]) + seg_mask.to_bytes(mask_bytes, "little")[:13])


def build_diy_activate(code: int, music_code: int = 0) -> bytes:
    """33 05 0A <code u2le> <music_code u2le> activate DIY (music_code 0 == legacy 3-byte form).
    split_h6047/com/govee/h6047/ble/SubModeNewDiy.java:29 (getWriteBytes);
    base-classes11/com/govee/base2light/effectPlay/effect/mode/SubModeNewDiy.java:64 (getWriteBytes,
    e false).
    """
    return append_bcc(
        bytes(
            [0x33, 0x05, 0x0A, code & 0xFF, (code >> 8) & 0xFF, music_code & 0xFF, (music_code >> 8) & 0xFF]
        )
    )


def build_diy_activate_custom_music(code: int, music_code: int, music_type: int) -> bytes:
    """33 05 0A <code u2le> <music u4le> <type> activate DIY with custom music.
    base-classes11/com/govee/base2light/effectPlay/effect/mode/SubModeNewDiy.java:64 (getWriteBytes,
    e true :65-68).
    """
    m = music_code
    return append_bcc(
        bytes(
            [
                0x33,
                0x05,
                0x0A,
                code & 0xFF,
                (code >> 8) & 0xFF,
                m & 0xFF,
                (m >> 8) & 0xFF,
                (m >> 16) & 0xFF,
                (m >> 24) & 0xFF,
                music_type & 0xFF,
            ]
        )
    )


def build_game_mode_h6057(game_type: int) -> bytes:
    """33 05 0B <game_type> H6057 game sub-mode, game_type 0..3.
    split_hollowlamp/com/govee/h6057/origin/newDetail/H6057DetailVM.java:657-672 (H6057DetailVM.B5).
    """
    return append_bcc(bytes([0x33, 0x05, 0x0B, game_type & 0xFF]))


def build_mic_beat_color(r: int, g: int, b: int) -> bytes:
    """33 05 05 00 R G B mic beat colour.
    base-classes11/com/govee/base2light/pact/newdetail/content/mic/MicController.java:41 (MicController.q);
    split_bulblightstringv1/com/govee/bulblightv3/ble/MicController.java:26 (MicController.q).
    """
    return append_bcc(bytes([0x33, 0x05, 0x05, 0x00, r & 0xFF, g & 0xFF, b & 0xFF]))


def build_mic_enter() -> bytes:
    """33 05 05 01 enter mic mode (auto colour).
    split_bulblightstringv1/com/govee/bulblightv3/ble/SubModeMic.java:17 (SubModeMic.getWriteBytes);
    base-classes11/com/govee/base2light/pact/newdetail/content/mic/SubModeMic.java:97 (getWriteBytes);
    base-classes11/com/govee/base2light/pact/newdetail/content/mic/MicController.java:41 (MicController.q,
    no-arg).
    """
    return append_bcc(bytes([0x33, 0x05, 0x05, 0x01]))


def build_mic_h6165() -> bytes:
    """33 05 05 00 01 mic mode, H6165/H6166 (flag always 1).
    split_rgblight/com/govee/h6165/ble/MicController.java:20 (MicController.q); only built as
    MicController(true) at split_rgblight/com/govee/h6165/sku/AbsEffectOp4Ble.java:186.
    """
    return append_bcc(bytes([0x33, 0x05, 0x05, 0x00, 0x01]))


def build_mic_h6192() -> bytes:
    """33 05 05 mic mode, H6192 (sub byte only).
    split_rgblight/com/govee/stringlightv2/ble/SubModeMicH6192.java:12 (SubModeMicH6192.getWriteBytes).
    """
    return append_bcc(bytes([0x33, 0x05, 0x05]))


def build_mic_mode_color(r: int, g: int, b: int) -> bytes:
    """33 05 05 01 01 R G B mic mode with a fixed colour.
    base-classes11/com/govee/base2light/pact/newdetail/content/mic/SubModeMic.java:97 (getWriteBytes,
    c true); split_rgbiclight/com/govee/chassislightv1/ble/SubModeMic.java:32 (getWriteBytes).
    """
    return append_bcc(bytes([0x33, 0x05, 0x05, 0x01, 0x01, r & 0xFF, g & 0xFF, b & 0xFF]))


def build_mic_v4(dynamic: bool) -> bytes:
    """33 05 05 03 <dynamic> mic dynamic flag.
    base-classes11/com/govee/base2light/pact/newdetail/content/mic/MicControllerV4.java:20
    (MicControllerV4.q); split_rgbiclight/com/govee/chassislightv1/ble/MicController.java:20;
    split_rgblight/com/govee/h73101217/ble/MicController.java:20.
    """
    return append_bcc(bytes([0x33, 0x05, 0x05, 0x03, 1 if dynamic else 0]))


def build_mic_via_music_0x13() -> bytes:
    """33 05 13 10 mic mode for chassislightv1 BK / H7092.
    split_rgbiclight/com/govee/chassislightv1/ble/SubModeMicV2.java:11 (SubModeMicV2.getWriteBytes).
    """
    return append_bcc(bytes([0x33, 0x05, 0x13, 0x10]))


def build_music_code_only(code: int) -> bytes:
    """33 05 13 <code> new-music apply with no sensitivity byte, sent right after the MultipleController4Music
    multi-packet that carries sensitivity.
    split_h6061/com/govee/cubelight/newdetails/music/H6061CubeMusicMode.java:181-184,229
    (H6061CubeMusicMode.d);
    split_h6061/com/govee/cubelight/newdetails/music/H6069CubeMusicMode.java:280-282,318
    (H6069CubeMusicMode.d).
    """
    return append_bcc(bytes([0x33, 0x05, 0x13, code & 0xFF]))


def build_music_color(
    code: int, sensitivity: int, rgb: Sequence[int] | None = None, *, sub_mode: int
) -> bytes:
    """33 05 <sub_mode> <code> <sens> <flag> [R G B] music with colour, no dynamic byte; rgb=None -> flag 0.
    split_bulblightstringv1/com/govee/bulblightstringv1/ble/SubModeMusic.java:56 (0x0E);
    split_hollowlamp/com/govee/tablelampv1/newAdjust/h6052/H6052MusicMode.java:69 (0x0F);
    split_rgbiclight/com/govee/barelightv1/ble/SubModeMusic.java:72 (0x0C);
    split_rgbiclight/com/govee/dreamcolorlightv1/ble/SubModeMusic.java:78 (0x11).
    """
    body = [0x33, 0x05, sub_mode, code, sensitivity, 0 if rgb is None else 1]
    if rgb is not None:
        body += list(rgb)
    return append_bcc(bytes(b & 0xFF for b in body))


def build_music_dyn_before_sens(code: int, sensitivity: int, *sub_effects: int) -> bytes:
    """33 05 13 <code> <sub_effects...> <sens> music with the sub-effect (dynamic) byte(s) BEFORE sensitivity;
    hollowlamp V3 always sends one sub byte (dyn for codes 75/76, else 0).
    split_hollowlamp/com/govee/hollowlamp/ble/SubModeMusicV3.java:83 (SubModeMusicV3.getWriteBytes);
    split_hollowlamp/com/govee/hollowlamp/newdetails/H6059MusicMode.java:67 (H6059MusicMode.d).
    """
    return append_bcc(
        bytes([0x33, 0x05, 0x13, code & 0xFF, *[s & 0xFF for s in sub_effects], sensitivity & 0xFF])
    )


def build_music_dyn_color(
    code: int, sensitivity: int, dyn_byte: int, rgb: Sequence[int] | None = None, *, sub_mode: int = 0x13
) -> bytes:
    """33 05 <sub_mode> <code> <sens> <dyn_byte> <flag> [R G B] music with dynamic byte; rgb=None -> flag 0,
    no RGB. split_rgbiclight/com/govee/rgbiclight/mode/music/MusicMode.java:82 (MusicMode.d);
    split_rgbiclight/com/govee/rgbiclight/Protocols.java:92 (Protocols.d);
    split_h61d3/com/govee/h61d3/adjustNew/music/MusicMode.java:183 (MusicMode.d);
    split_hollowlamp/com/govee/tablelampv1/newAdjust/h6052/H6052MusicMode.java:69 (code 4, sub 0x0F).
    """
    body = [0x33, 0x05, sub_mode, code, sensitivity, dyn_byte, 0 if rgb is None else 1]
    if rgb is not None:
        body += list(rgb)
    return append_bcc(bytes(b & 0xFF for b in body))


def build_music_h6047(
    sensitivity: int, code: int, sub0: int = 0, sub1: int = 0, *, headphone: bool | None = None
) -> bytes:
    """33 05 13 <sens> <code> <x> <y> [pickup] H6047 music (sensitivity first); code 75: x=sub0,y=sub1 else
    x=0,y=sub0; headphone only for goodsType 119. split_h6047/com/govee/h6047/detail/mode/MusicMode.java:92
    (MusicMode.d).
    """
    x, y = (sub0, sub1) if code == 75 else (0, sub0)
    body = [0x33, 0x05, 0x13, sensitivity, code, x, y]
    if headphone is not None:
        body.append(1 if headphone else 0)
    return append_bcc(bytes(b & 0xFF for b in body))


def build_music_h6127_newdetail(
    code: int, sensitivity: int, rgb: Sequence[int] = (0xFF, 0x00, 0x00)
) -> bytes:
    """0x33 05 01 [code, sensitivity, (R,G,B unless code is 0 or 3)] old-music write, H6127 newdetail.
    OldMusicMode.a0 / P (split_rgbiclight/com/govee/h6127/newdetail/OldMusicMode.java:131-134,207-211);
    colour = ColorUtils.n (base-classes10/com/govee/base2kt/utils/ColorUtils.java:314-316)."""
    body = [0x33, 0x05, 0x01, code & 0xFF, sensitivity & 0xFF]
    if code not in (0, 3):
        body += [c & 0xFF for c in rgb]
    return append_bcc(bytes(body))


def build_music_h6148(
    code: int, sensitivity: int, dyn: int, color_flag: int, rgb: tuple[int, int, int] = (0xFF, 0x00, 0x00)
) -> bytes:
    """33 05 0F <code clamped 0..1> <dyn> <sens> <color_flag> <R> <G> <B> H6148 music; RGB is always sent (app
    default red). split_rgblight/com/govee/homelightv1/newdetail/mode/H6148OldMusicMode.java:244-249
    (H6148OldMusicMode.b0), sent by H6148OldMusicMode.Q :169-185.
    """
    r, g, b = rgb
    return append_bcc(
        bytes(
            v & 0xFF for v in [0x33, 0x05, 0x0F, max(0, min(code, 1)), dyn, sensitivity, color_flag, r, g, b]
        )
    )


def build_music_h61a8(code: int, sensitivity: int, sub_effects: Sequence[int] = ()) -> bytes:
    """33 05 13 - H61A8 (gt 207) music apply for codes 5/4/6 (energy/spectrum/scroll) and 3 (rhythm); colours
    are Android ARGB ints. split_rgbiclight/com/govee/rgbiclight/mode/music/MusicMode.java:82-135
    (MusicMode.d); sub-mode split_rgbiclight/com/govee/rgbiclight/Protocols.java:57-71.
    """
    p = [0x33, 0x05, 0x13, code & 0xFF, sensitivity & 0xFF]
    if code in (5, 4, 6):
        p.append(0x00)
        if sub_effects:
            p.append(sub_effects[0] & 0xFF)
            if sub_effects[0] != 0:
                c = sub_effects[1]
                p += [(c >> 16) & 0xFF, (c >> 8) & 0xFF, c & 0xFF]
    elif code == 3:
        p += [sub_effects[0] & 0xFF, sub_effects[1] & 0xFF]
        if sub_effects[1] != 0:
            c = sub_effects[2]
            p += [(c >> 16) & 0xFF, (c >> 8) & 0xFF, c & 0xFF]
    return append_bcc(bytes(p))


def build_op15_brightness(pct: int, seg_mask: int, *, mask_bytes: int = 2) -> bytes:
    """33 05 15 02 [pct][seg_mask LSB-first, u16le up to 16 positions, ceil(n/8) bytes above] (Bytes.G
    base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Bytes.java:172-181 via Config.JR
    base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Config.java:10567-10570;
    SubModeColorV1 opType 0 split_h60ax/com/govee/pact_h60a0/ble/v1/SubModeColorV1.java:674-683;
    split_h6047/com/govee/h6047/ble/SubModeColor.java:177-182).
    """
    return append_bcc(bytes([0x33, 0x05, 0x15, 0x02, pct & 0xFF]) + seg_mask.to_bytes(mask_bytes, "little"))


def build_op15_brightness_group(group: int, levels: Sequence[int]) -> bytes:
    """33 05 15 03 <group> <up to 14 levels> per-group brightness.
    base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Bytes.java:202 (Bytes.I);
    split_h60ax/com/govee/pact_h60a0/ble/v1/SubModeColorV1.java:702 (opType 6).
    """
    return append_bcc(
        bytes([0x33, 0x05, 0x15, 0x03, group & 0xFF, *[lvl & 0xFF for lvl in list(levels)[:14]]])
    )


def build_op15_brightness_set(levels: Sequence[int]) -> bytes:
    """33 05 15 03 <up to 15 levels> brightness set, no group index.
    base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Bytes.java:153 (Bytes.F).
    """
    return append_bcc(bytes([0x33, 0x05, 0x15, 0x03, *[lvl & 0xFF for lvl in list(levels)[:15]]]))


def build_op15_color_group(group: int, colors: Sequence[Sequence[int]]) -> bytes:
    """33 05 15 04 <group> <up to 4 x RGB> per-group colours.
    base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Bytes.java:393 (Bytes.m);
    split_h60ax/com/govee/pact_h60a0/ble/v1/SubModeColorV1.java:711 (opType 7).
    """
    flat = [c & 0xFF for rgb in list(colors)[:4] for c in rgb]
    return append_bcc(bytes([0x33, 0x05, 0x15, 0x04, group & 0xFF, *flat]))


def build_op15_color_temp_04(kelvin: int) -> bytes:
    """33 05 15 04 <kelvin u2be> colour temperature, colour-instruction version 9.
    base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Bytes.java:133-139 (Bytes.D) via
    Config.java:10543-10545 (Config.JM, N[9] at :5640-5644) and ColorModeControllerConfig.java:499-525
    (Companion.h).
    """
    return append_bcc(bytes([0x33, 0x05, 0x15, 0x04, (kelvin >> 8) & 0xFF, kelvin & 0xFF]))




def build_op15_color_v5(
    r: int, g: int, b: int, seg_mask: int, *, kelvin: int = 0, mask_bytes: int = 2
) -> bytes:
    """33 05 15 01 [R,G,B][kelvin u16be, 0 for RGB][seg_mask LSB-first, mask_bytes long] (colour version 5
    COLOR_MODE_0X15_NO_TEMP_RGB: Config.J[5] = Config.Zt
    base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Config.java:5527-5531,13124-13131 ->
    Bytes.l for an RGB pick, Bytes.u for a Kelvin tint
    base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Bytes.java:379-390,541-556).
    The app sizes the mask by the position count: 2 bytes up to 16 positions, else ceil(n/8)
    (makeSelectedTwoBytes / BleUtils.X); pass that as mask_bytes.
    """
    return append_bcc(
        bytes([0x33, 0x05, 0x15, 0x01, r, g, b, (kelvin >> 8) & 0xFF, kelvin & 0xFF])
        + seg_mask.to_bytes(mask_bytes, "little")
    )


_OP15_N_MAX_MASK_BYTES = 17 - 11  # Bytes.n: the mask starts at extBytes[11] of 17 and is not coerced


def build_op15_gradient_color(colors: Sequence[Sequence[int]], pos_set: Sequence[bool]) -> bytes:
    """33 05 15 05 <3 x RGB> <segment mask LE> gradient colour, GradientColor version 0.
    base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Bytes.java:404-420 (Bytes.n) via
    GradientColorControllerConfig.java:117-121 (n, e[0]) and :284-359 (k, ControllerMode.Companion.e).
    The mask is 2 bytes up to 16 positions, else ceil(n/8) (BleUtils.X). Bytes.n copies it uncoerced into
    a 17-byte array at offset 11 (:417-418), so more than 6 bytes (> 48 positions) throws
    ArrayIndexOutOfBounds in the app: one frame, no second frame and no cap. ValueError here likewise.
    (GradientColorControllerConfig.k takes Bytes.r instead when every colour is the same Kelvin tint, :334;
    see build_op15_color_temp_bytes_r, whose 7-byte mask reaches 56 positions.)
    """
    rgb = [c & 0xFF for col in colors for c in col]
    if len(rgb) != 9:
        raise ValueError("expected exactly 3 colors (9 RGB bytes)")
    mask = op15_mask(pos_set)
    if len(mask) > _OP15_N_MAX_MASK_BYTES:
        raise ValueError(
            f"{len(pos_set)} positions need a {len(mask)}-byte mask; Bytes.n fits at most"
            f" {_OP15_N_MAX_MASK_BYTES} (48 positions)"
        )
    return append_bcc(bytes([0x33, 0x05, 0x15, 0x05, *rgb]) + mask)


def build_op15_color_bytes_h(r: int, g: int, b: int, pos_set: Sequence[bool]) -> bytes:
    """33 05 15 01 [R,G,B][00 x5][mask] RGB on the positions set in pos_set (Bytes.h
    base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Bytes.java:315-326: a 17-byte
    ext array, RGB at 2..4, mask at 10 = 2 bytes up to 16 positions, else BleUtils.X ceil(n/8),
    coerceAtMost(len, 17 - 10) = at most 7 bytes, so positions 56+ are silently dropped). Reached from
    Config.Yt (Config.java:12960-12968) when the colour is not a Kelvin tint, ColorControllerConfig
    (split_rgbiclight .../mode/color/ColorControllerConfig.java:87), h61d3 SubModeColor.java:130 and
    h60ax ControllerBuild.java:44-52. Equals library wire.build.color_rgb / segment_rgb ("h60a6").
    """
    mask = op15_mask(pos_set)[:OP15_MAX_MASK_BYTES]
    return append_bcc(bytes([0x33, 0x05, 0x15, 0x01, r & 0xFF, g & 0xFF, b & 0xFF, 0, 0, 0, 0, 0]) + mask)


_OP15_BASIC_MAX_MASK_BYTES = 17 - 5  # the ext [15 01 R G B] + mask must fit frame bytes 2..18


def build_op15_color_basic(r: int, g: int, b: int, pos_set: Sequence[bool]) -> bytes:
    """33 05 15 01 [R,G,B][mask] RGB on the positions set in pos_set, the basic form (form_15_01 basic):
    SubModeColorV1.getWriteBytes opType 1
    split_h60ax/com/govee/pact_h60a0/ble/v1/SubModeColorV1.java:670-672,684-693. The ext is [15 01 R G B]
    and the mask follows the colour directly: 2 bytes for a device of up to 16 segments
    (makeSelectedTwoBytes), else BleUtils.X ceil(n/8); pos_set holds one entry per segment. The mode
    controller copies the ext whole into the 20-byte frame (AbsSingleController.java:19 ->
    base-classes10/com/govee/base2kt/utils/BleUtils.java:1030-1041), so more than 12 mask bytes (over
    96 segments) do not fit before the BCC: ValueError.
    """
    mask = op15_mask(pos_set)
    if len(mask) > _OP15_BASIC_MAX_MASK_BYTES:
        raise ValueError(
            f"{len(pos_set)} positions need a {len(mask)}-byte mask; the basic form fits at most"
            f" {_OP15_BASIC_MAX_MASK_BYTES} (96 positions)"
        )
    return append_bcc(bytes([0x33, 0x05, 0x15, 0x01, r & 0xFF, g & 0xFF, b & 0xFF]) + mask)


def build_op15_color_temp_bytes_r(
    tint: Sequence[int], kelvin: int, pos_set: Sequence[bool], kind: int = 1
) -> bytes:
    """33 05 15 01 [slot RGB][kelvin u16be][tint RGB][mask] colour temperature on pos_set (Bytes.r
    base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Bytes.java:474-501; the app's
    tint is the picked colour or Constant.getTemColorByKelvin(K)). kind (Bytes.r's kind argument, :480-486):
    0 -> slot 00 00 00; 3 -> slot 00 00 00 when every position is set, else slot = tint with kelvin and
    tint zeroed (:492, :495); any other value (1, the usual one, or ColorUtils.R()) -> slot ff ff ff.
    Mask as Bytes.h (at ext byte 10, at most 7 bytes). Callers: Config.Yt (Config.java:12967),
    GradientColorControllerConfig.k (:334, kind 1), h60ax ControllerBuild.java:55-63 (kind 1).
    kind 1 equals library wire.build.segment_color_temp / color_temp ("h60a6").
    """
    partial = not all(pos_set)  # Bytes.x(): true when any position is unset (false for an empty set)
    t = [c & 0xFF for c in tint]
    if len(t) != 3:
        raise ValueError("tint must be 3 bytes (R, G, B)")
    if kind == 0:
        slot = [0, 0, 0]
    elif kind == 3:
        slot = t if partial else [0, 0, 0]
    else:
        slot = [0xFF, 0xFF, 0xFF]
    k = 0 if kind == 3 and partial else kelvin
    tail = [0, 0, 0] if kind == 3 and partial else t
    mask = op15_mask(pos_set)[:OP15_MAX_MASK_BYTES]
    return append_bcc(bytes([0x33, 0x05, 0x15, 0x01, *slot, (k >> 8) & 0xFF, k & 0xFF, *tail]) + mask)


def build_op15_kelvin_05(kelvin: int) -> bytes:
    """33 05 15 05 [kelvin u16be], H60A0 CCT (ControllerBuildH60A0.b/d
    split_h60ax/com/govee/pact_h60a0/adjust/h60a0/ControllerBuildH60A0.java:62-66,93-99; SubModeColorV1
    opType 2 split_h60ax/com/govee/pact_h60a0/ble/v1/SubModeColorV1.java:695-697).
    """
    return append_bcc(bytes([0x33, 0x05, 0x15, 0x05, (kelvin >> 8) & 0xFF, kelvin & 0xFF]))


def build_op15_relative_brightness(value: int, pos_set: Sequence[bool] = (True, True)) -> bytes:
    """33 05 15 05 <side> <value u8> relative brightness, RelativeBrightnessVersion 1 (goodsType 80 only).
    base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Bytes.java:184-199 (Bytes.H) via
    Config.java:10727-10730 (Config.KR, L[1]) and ColorModeControllerConfig.java:1069-1097 (Companion.t).
    """
    p = list(pos_set)
    side = 3 if len(p) < 2 or (p[0] and p[1]) else (2 if not p[0] else 1)
    return append_bcc(bytes([0x33, 0x05, 0x15, 0x05, side, value & 0xFF]))


def build_op15_set_mode() -> bytes:
    """33 05 15 00 switch to colour mode (legacy H60A0/H60A1).
    split_h60ax/com/govee/pact_h60a0/ble/v1/SubModeColorV1.java:699 (getWriteBytes opType 3);
    split_h60ax/com/govee/pact_h60a0/ble/controller/compose/ComposeChange2ColorMode.java:32.
    """
    return append_bcc(bytes([0x33, 0x05, 0x15, 0x00]))


def build_op15_side_brightness(pct: int, first: bool = True, second: bool = True) -> bytes:
    """33 05 15 05 [side 3 both / 2 first unselected / 1 first only][pct], relative-brightness version 1
    (Bytes.H base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Bytes.java:184-199 via
    Config.KR base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Config.java:10727-10730).
    """
    side = 3 if (first and second) else (2 if not first else 1)
    return append_bcc(bytes([0x33, 0x05, 0x15, 0x05, side, pct & 0xFF]))


def build_operate_mode_20() -> bytes:
    """0x05/0x20 H6092-family operate-mode activate [0x20] (sent after the A3 comm 0x56 operate-parameter
    upload). base-classes11/com/govee/base2light/pact/ble/SubModeOperate.java:227-230,423-425
    (getWriteBytes/subModeCommandType);
    base-classes4/com/govee/starProjector/ControlConfig4H609X.java:84-96 (e).
    """
    return append_bcc(bytes([0x33, 0x05, 0x20]))


def build_scene_ext_carousel(effect: int, music_code: int = 0, voice_type: int = 0) -> bytes:
    """33 05 04 <effect u2le> <music u4le> <voiceType> extended scene frame, built only for the H6057 carousel
    effect string, never sent by a scene tab.
    base-classes11/com/govee/base2light/pact/newdetail/content/scene/SubModeScenes.java:60-69
    (getWriteBytes, e true) via
    base-classes11/com/govee/base2light/pact/newdetail/content/carousel/CarouselEffectManager.java:892-927
    (l).
    """
    return append_bcc(
        bytes(
            [
                0x33,
                0x05,
                0x04,
                effect & 0xFF,
                (effect >> 8) & 0xFF,
                *[(music_code >> s) & 0xFF for s in (0, 8, 16, 24)],
                voice_type & 0xFF,
            ]
        )
    )


def build_scene_newdetail(effect: int, music_code: int = 0) -> bytes:
    """33 05 04 <effect u2le> <music u2le> newdetail scene-tab activate; music_code is sent only when the
    scene's music switch is on, else 0.
    base-classes11/com/govee/base2light/pact/newdetail/content/scene/SubModeScenes.java:59-77
    (getWriteBytes, e false) via BaseSceneViewMode.java:722-740 (Z2) and :864-924 (k3).
    """
    return append_bcc(
        bytes(
            [
                0x33,
                0x05,
                0x04,
                effect & 0xFF,
                (effect >> 8) & 0xFF,
                music_code & 0xFF,
                (music_code >> 8) & 0xFF,
            ]
        )
    )


def build_video_h6101(dynamic: bool, part_screen: bool) -> bytes:
    """33 05 00 <effect: 2 dynamic, 0 soft> <area: 0 part of the screen, 1 all of it> video mode, H6101.
    split_rgblight/com/govee/h6101/ble/SubModeVideo.java:38 (SubModeVideo.getWriteBytes);
    split_rgblight/com/govee/h6101/adjust/VideoFragment.java:37-58 and
    base-classes11/com/govee/base2light/light/v1/AbsVideoFragmentV1.java:39-79 (the dynamic / soft and
    part / all buttons).
    """
    return append_bcc(bytes([0x33, 0x05, 0x00, 2 if dynamic else 0, 0 if part_screen else 1]))


def build_video_h6104(part_screen: bool, sensitivity: int) -> bytes:
    """33 05 00 00 <area: 0 part of the screen, 1 all of it> <sensitivity> video mode, H6104.
    split_rgblight/com/govee/h6104/ble/SubModeVideo.java:38 (SubModeVideo.getWriteBytes);
    split_rgblight/com/govee/h6104/adjust/VideoFragment.java:37-58 and
    base-classes11/com/govee/base2light/light/v1/AbsVideoFragmentV2.java:39-72 (the part / all buttons and
    the sensitivity slider).
    """
    return append_bcc(bytes([0x33, 0x05, 0x00, 0x00, 0 if part_screen else 1, sensitivity & 0xFF]))


def build_diy_old_07(
    diy_code: int, effect: int, sub_effect: int, speed: int, colors: Sequence[Sequence[int]] = ()
) -> bytes:
    """0x33 05 07 [diyCode, effect, subEffect, speed, <=4 x RGB] old DIY (SubModeOldDiy ctor/getWriteBytes,
    split_rgbiclight/com/govee/h6127/ble/SubModeOldDiy.java:16-51,63-65;
    split_rgbiclight/com/govee/h6129/ble/SubModeOldDiy.java:19-43,53-59).
    """
    cols = list(colors)
    if len(cols) > 4:
        raise ValueError("SubModeOldDiy holds at most 4 colours")
    body = [0x07, diy_code, effect, sub_effect, speed]
    for c in cols:
        body += list(c)
    return append_bcc(bytes([0x33, 0x05] + [b & 0xFF for b in body]))


def read_device_other_info() -> bytes:
    """AA 07 08 OTHER_INFO read, reply [0x08][brightness][flags: bit1 main/first switch, bit2 second switch].
    split_h60ax/com/govee/pact_h60a0/ble/controller/compose/ControllerOtherInfo.java:24-26,29-31,39-41;
    parsers split_h60ax/com/govee/pact_h60a0/adjust/v1/ComposeReadAllInfoV1.java:76-85,
    split_h60ax/com/govee/pact_h60a0/adjust/h60a0/VM4LightH60A0.java:125-131,582-591,
    split_bulblightstringv1/com/govee/bulblightv3/newdetail/NewDetailVm.java:266-279,865-870.
    """
    return read_request(0x07, 0x08)


def read_device_sn() -> bytes:
    """0xAA 07 02 device UID read, reply [0x02][uid 8 B]
    (base-classes10/com/govee/base2light/ble/controller/SnController.java:32/:37-39
    SnController.getCommandType/p; split_h61d3/com/govee/h61d3/ble/controller/SnController.java:55,69).
    """
    return read_request(0x07, 0x02)


def read_dsp_version() -> bytes:
    """0xAA 07 07 DSP-version read, reply [0x07][dsp u16le]
    (base-classes10/com/govee/base2light/ble/controller/DspVersionInDeviceInfoController.java:15,21; kt
    ControllerDspVersionInDeviceInfo.java:64,67; split_h60ax compose DspVersionController.java:6,9).
    """
    return read_request(0x07, 0x07)


def read_hard_version() -> bytes:
    """0xAA 07 03 hardware-version read, reply [0x03][ASCII]
    (base-classes10/com/govee/base2light/ble/controller/HardVersionController.java:23/:29; kt
    ControllerHardVersion.java:28,31; split_h60ax compose HardVersionController.java:6,9; split_h61d3
    SoftHardController.java:56,82).
    """
    return read_request(0x07, 0x03)


def read_mcu_hard_version() -> bytes:
    """0xAA 07 0B MCU hardware-version read, reply ASCII
    (base-classes10/com/govee/base2light/ble/controller/McuHardVersionControllerV1.java:26,33
    McuHardVersionControllerV1.getCommandType; base-classes12 base_h71xx McuHardVersionController.java:18).
    """
    return read_request(0x07, 0x0B)


def read_mcu_soft_version() -> bytes:
    """0xAA 07 0A MCU software-version read, reply ASCII
    (base-classes10/com/govee/base2light/ble/controller/McuSoftVersionControllerV1.java:26,33
    McuSoftVersionControllerV1.getCommandType; base-classes12 base_h71xx McuSoftVersionController.java:18).
    """
    return read_request(0x07, 0x0A)


def read_soft_version_in_device_info() -> bytes:
    """0xAA 07 04 software-version read via device-info, reply [0x04][ASCII]
    (split_h61d3/com/govee/h61d3/ble/controller/SoftHardController.java:68,82
    SoftHardController.getCommandType;
    base-classes10/com/govee/base2light/ble/controller/SoftVersionInDeviceInfoController.java:13,19).
    """
    return read_request(0x07, 0x04)


def build_h6101_calibration() -> bytes:
    """0x33 08 (no payload) H6101 calibration start (CalibrationController,
    split_rgblight/com/govee/h6101/ble/CalibrationController.java:11-17; caller
    split_rgblight/com/govee/h6101/adjust/CalibrationAc.java:139).
    """
    return append_bcc(bytes([0x33, 0x08]))


def build_string_scene_09(index: int) -> bytes:
    """0x33 05 09 [index+1, 15 x 00] string-light scene (SubModeScenes.getWriteBytes,
    split_bulblightstringv1/com/govee/bulblightstringv1/ble/SubModeScenes.java:22-27,49-51;
    split_bulblightstringv1/com/govee/h7022/adjust/mode/SubModeScenes.java:41-46).
    """
    return append_bcc(bytes([0x33, 0x05, 0x09, (index + 1) & 0xFF]))


def build_sync_time_stamp(unix_seconds: int, tz_hour: int, tz_min: int) -> bytes:
    """33 09 timestamp sync time, TimerConfig version 1
    (base-classes11/com/govee/base2light/kt/general_controller/ControllerSyncTimeStamp.java:29-32,44-48).
    """
    return append_bcc(
        bytes([0x33, 0x09])
        + (unix_seconds & 0xFFFFFFFF).to_bytes(4, "big")
        + bytes([0x01, tz_hour & 0xFF, tz_min & 0xFF])
    )


def build_sync_time_v0(
    hour: int,
    minute: int,
    second: int,
    week: int,
    tz_hour: int,
    tz_min: int,
    day: int,
    month: int,
    year: int,
    unix_seconds: int,
) -> bytes:
    """33 09 15-byte sync time, TimerConfig version 0/default
    (base-classes11/com/govee/base2light/kt/general_controller/ControllerSyncTime.java:31-35 Companion.a).
    """
    return append_bcc(
        bytes(
            [
                0x33,
                0x09,
                hour & 0xFF,
                minute & 0xFF,
                second & 0xFF,
                week & 0xFF,
                0x01,
                tz_hour & 0xFF,
                tz_min & 0xFF,
                day & 0xFF,
                month & 0xFF,
                year & 0xFF,
                (year >> 8) & 0xFF,
            ]
        )
        + (unix_seconds & 0xFFFFFFFF).to_bytes(4, "little")
    )


def read_daysync_device_info() -> bytes:
    """0xAC 0x09 multi-reply read: storage figures and all on-device schemes.
    ControllerSingleRead.makeSingleRead(9)
    (base-classes11/com/govee/base2light/kt/general_controller/ControllerSingleRead.java:44-72) in
    DaySyncVM.Companion.f
    (base-classes11/com/govee/base2light/pact/newdetail/content/daySync/DaySyncVM.java:319-323).
    """
    return append_bcc(bytes([0xAC, 0x09]))


def build_auto_time(
    group: int,
    on: bool,
    open_hour: int,
    open_min: int,
    close_hour: int,
    close_min: int,
    repeat: int,
) -> bytes:
    """0x33 0A legacy auto on/off timer [on, openH, openM, closeH, closeM, group, repeat]
    (AutoTimeController.q:62-64; kt ControllerTimer:52-55; ControllerNewTimer.Companion.c:46-49 via
    Controller4ExtBytes.d).
    """
    return append_bcc(
        bytes(
            [
                0x33,
                0x0A,
                1 if on else 0,
                open_hour & 0xFF,
                open_min & 0xFF,
                close_hour & 0xFF,
                close_min & 0xFF,
                group & 0xFF,
                repeat & 0xFF,
            ]
        )
    )


def read_auto_time(group: int) -> bytes:
    """0xAA 0A [group] auto-timer read (AutoTimeController.p:50-52; kt ControllerTimer(byte,int):42-46); apps
    read groups 0 and 1.
    """
    return read_request(0x0A, group)


def build_delay_close(on: bool, minutes: int) -> bytes:
    """0x33 0B countdown auto-off [on, hours, minutes] from total minutes (DelayCloseController.q:46-50; kt
    ControllerDelayClose(write, on, minutes):74-76; TimeUtils.getHM:45-48).
    """
    m = max(0, minutes)
    return append_bcc(bytes([0x33, 0x0B, 1 if on else 0, (m // 60) & 0xFF, (m % 60) & 0xFF]))


def build_game_h6057(game_code: int) -> bytes:
    """0x33 05 0B [gameCode] H6057 game mode (H6057DetailVM.B5,
    split_hollowlamp/com/govee/h6057/origin/newDetail/H6057DetailVM.java:657-662, from H6057GameDelegate
    split_hollowlamp/com/govee/h6057/origin/newDetail/H6057GameDelegate.java:254-260; legacy
    SubModeGame.getWriteBytes split_hollowlamp/com/govee/h6057/origin/ble/SubModeGame.java:40-41,60-61).
    """
    return append_bcc(bytes([0x33, 0x05, 0x0B, game_code & 0xFF]))


def read_delay_close() -> bytes:
    """0xAA 0B, no selector (DelayCloseController():11-13, p() null; kt
    ControllerDelayClose.Companion.b:51-53).
    """
    return read_request(0x0B)


def read_mode_bare() -> bytes:
    """0xAA 05 mode read with no selector: the pact_h60a0 adjust-v1 connect read. The compose
    ModeController.Companion.a passes ext null
    (split_h60ax/com/govee/pact_h60a0/ble/controller/compose/ModeController.java:26-28), and the compose
    AbsController.getNextCommBytes frames it as BleUtils.Companion.p(0xAA, 5, null)
    (split_h60ax/com/govee/pact_h60a0/ble/controller/compose/AbsController.java:23-26;
    base-classes10/com/govee/base2kt/utils/BleUtils.java:1030-1041): no selector byte. Caller
    ComposeReadAllInfoV1 (split_h60ax/com/govee/pact_h60a0/adjust/v1/ComposeReadAllInfoV1.java:148), from
    BleOpV1.V (split_h60ax/com/govee/pact_h60a0/adjust/v1/BleOpV1.java:47-51).
    NOT the H6057 newdetail connect read: kt ControllerMode.Companion.b passes the synthetic-ctor mask 2, so
    its ext defaults to [01] (base-classes11/com/govee/base2light/kt/general_controller/ControllerMode.java:
    45-47, 107-109), and Compose4InfoBleIotH6057$iControllerStrategy$1.java:165 sends AA 05 01 (read_mode).
    """
    return read_request(0x05)


def build_plant_red_blue(red: int, blue: int = 1) -> bytes:
    """0x33 0D [red, blue]: grow-light red:blue ratio; the live H7004/H7017 screen sends (ratio, 1).
    split_rgblight/com/govee/rgblight/h7004/ConnectSuccess4Plant.java:392-400 (ConnectSuccess4Plant.J ->
    Controller4ExtBytes.Companion.d([13, i, 1]));
    base-classes11/com/govee/base2light/kt/general_controller/Controller4ExtBytes.java:85-100
    (Companion.d).
    """
    return append_bcc(bytes([0x33, 0x0D, red & 0xFF, blue & 0xFF]))


def read_plant_red_blue() -> bytes:
    """0xAA 0D, no selector; reply [red u8][blue u8].
    split_rgblight/com/govee/rgblight/ControllerStrategyConfig.java:1345-1351 (k1:
    Controller4ExtBytes.Companion.c([13]), gated H7004/H7017 :1414-1416); reply parser r1 :753-765.
    """
    return read_request(0x0D)


def build_current_limit(on: bool) -> bytes:
    """33 0E <1|0> current-limit switch.
    base-classes11/com/govee/base2light/kt/general_controller/ControllerLimit.java:48-51,59-61,71-73;
    split_rgblight/com/govee/rgblight/SettingAc.java:113-125 (g4).
    """
    return append_bcc(bytes([0x33, 0x0E, 0x01 if on else 0x00]))


def read_current_limit() -> bytes:
    """AA 0E, no selector; reply frame[2] == 1 means on.
    base-classes11/com/govee/base2light/kt/general_controller/ControllerLimit.java:29-46 (Companion.b/c);
    split_rgblight/com/govee/rgblight/ControllerStrategyConfig.java:921-927 (B1).
    """
    return read_request(0x0E)


def build_light_num(n: int) -> bytes:
    """0x33 0F [n] light / bulb count (kt ControllerSegment ctor:61-63; bulblightstringv1 + h7022
    BulbNumController.q:32-34; h612526 ControllerLightNum ctor:31-41 emits the empty-payload form =
    build_light_num(0)).
    """
    return append_bcc(bytes([0x33, 0x0F, n & 0xFF]))


def build_light_num_h612526() -> bytes:
    """0x33 0F with no payload: what H6125/H6126 ControllerLightNum() emits on connect
    (ControllerLightNum.<init> split_rgbiclight/com/govee/h612526/newdetail/ControllerLightNum.java:31-45;
    Vm4Light.W4 split_rgbiclight/com/govee/h612526/newdetail/Vm4Light.java:508-514).
    """
    return append_bcc(bytes([0x33, 0x0F]))


def read_light_num() -> bytes:
    """0xAA 0F, no selector (kt ControllerSegment.Companion.b:40-48; dreamcolorlightv1 LightNumController:11;
    BulbNumController.getCommandType:22).
    """
    return read_request(0x0F)


def build_multi_wifi_a1(
    ssid: bytes,
    password: bytes,
    run_mode: int,
    tz_hours: int,
    iot_version: int,
    tz_minutes: int,
    api: bytes | None = None,
    matter: int | None = None,
    security: int | None = None,
) -> list[bytes]:
    """Wi-Fi provisioning upload, proType 0xA1 cmd 0x11: START [A1 11 00 packs 00], DATA [A1 11 i 16B], END
    [A1 11 FF]
    (base-classes10/com/govee/base2light/ble/controller/MultipleWifiController.java:20-57,59-104,164-198
    (ctors, x, y); base-classes10/com/govee/base2light/ble/comm/MultipleControllerComm.java:75-116
    (makeWriteMultipleBytes)).
    """
    value = bytes([len(ssid) & 0xFF]) + ssid + bytes([len(password) & 0xFF]) + password
    value += bytes([run_mode & 0xFF, tz_hours & 0xFF, iot_version & 0xFF, tz_minutes & 0xFF])
    tail = b""
    if matter is not None:
        tail += bytes([matter & 0xFF])
        if security is not None:
            tail += bytes([security & 0xFF])
    if api is not None:
        value += bytes([(len(api) >> 8) & 0xFF, len(api) & 0xFF]) + api
    elif tail:
        value += bytes([0x00, 0x00])
    value += tail
    packs = (len(value) + 15) // 16
    frames = [append_bcc(bytes([0xA1, 0x11, 0x00, packs & 0xFF, 0x00]))]
    for i in range(packs):
        frames.append(append_bcc(bytes([0xA1, 0x11, (i + 1) & 0xFF]) + value[i * 16 : (i + 1) * 16]))
    frames.append(append_bcc(bytes([0xA1, 0x11, 0xFF])))
    return frames


def build_sleep(enable: int, start_bri: int, close_time: int, cur_time: int) -> bytes:
    """0x33 11 V0 sleep [enable, startBri, closeTime, curTime] (SleepController.q:56-58; kt
    ControllerSleep(int,int,int,int):121-123 = TimerConfig sleep version 0).
    """
    return append_bcc(
        bytes([0x33, 0x11, enable & 0xFF, start_bri & 0xFF, close_time & 0xFF, cur_time & 0xFF])
    )


def build_sleep_color(
    enable: int,
    start_bri: int,
    close_time: int,
    cur_time: int,
    default_light: int,
    rgb: int,
    kelvin: int = 0,
    *,
    kelvin_be: bool = True,
) -> bytes:
    """0x33 11 colour sleep: V0 + [defaultLight, R, G, B, kelvin u16 BE (TimerConfig v1) or LE (v3)] (kt
    ControllerSleep.Companion.g:96-107; TimerConfig.e:148,152).
    """
    k = (
        bytes([(kelvin >> 8) & 0xFF, kelvin & 0xFF])
        if kelvin_be
        else bytes([kelvin & 0xFF, (kelvin >> 8) & 0xFF])
    )
    return append_bcc(
        bytes(
            [
                0x33,
                0x11,
                enable & 0xFF,
                start_bri & 0xFF,
                close_time & 0xFF,
                cur_time & 0xFF,
                default_light & 0xFF,
                (rgb >> 16) & 0xFF,
                (rgb >> 8) & 0xFF,
                rgb & 0xFF,
            ]
        )
        + k
    )


def build_sleep_effect(
    enable: int,
    start_bri: int,
    close_time: int,
    cur_time: int,
    open_effect: bool,
    effect_type: int,
    effect_code: int,
) -> bytes:
    """0x33 11 TimerConfig sleep v6: V0 + [openLampEffect, lampEffectType, effectCode u16 LE] (kt
    ControllerSleep.Companion.b:51-56).
    """
    return append_bcc(
        bytes(
            [
                0x33,
                0x11,
                enable & 0xFF,
                start_bri & 0xFF,
                close_time & 0xFF,
                cur_time & 0xFF,
                1 if open_effect else 0,
                effect_type & 0xFF,
                effect_code & 0xFF,
                (effect_code >> 8) & 0xFF,
            ]
        )
    )


def build_sleep_effect_lc_music(
    enable: int,
    start_bri: int,
    close_time: int,
    cur_time: int,
    open_effect: bool,
    effect_type: int,
    effect_code: int,
    open_voice: bool,
    voice_volume: int,
    voice_code: int,
) -> bytes:
    """0x33 11 TimerConfig sleep v8: V0 + [openLampEffect, lampEffectType, effectCode u16 LE, openVoice,
    voiceVolume, voiceCode u8] (kt ControllerSleep.Companion.f:88-93).
    """
    return append_bcc(
        bytes(
            [
                0x33,
                0x11,
                enable & 0xFF,
                start_bri & 0xFF,
                close_time & 0xFF,
                cur_time & 0xFF,
                1 if open_effect else 0,
                effect_type & 0xFF,
                effect_code & 0xFF,
                (effect_code >> 8) & 0xFF,
                1 if open_voice else 0,
                voice_volume & 0xFF,
                voice_code & 0xFF,
            ]
        )
    )


def build_sleep_fanlight(
    enable: int,
    light_open: int,
    start_bri: int,
    close_time: int,
    cur_time: int,
    default_light: int,
    rgb: int,
    kelvin: int,
    fan_open: int,
    fan_exe_open: int,
    fan_close_time: int,
    fan_close_cur_time: int,
    fan_gear: int,
) -> bytes:
    """0x33 11 TimerConfig sleep v7 (goodsType 359/368): [enable, lightOpen, startBri, closeTime, curTime,
    defaultLight, R, G, B, kelvin u16 BE, fanOpen, fanExeOpen, fanCloseTime, fanCloseCurTime, fanGear] (kt
    ControllerSleep.Companion.c:59-67).
    """
    return append_bcc(
        bytes(
            [
                0x33,
                0x11,
                enable & 0xFF,
                light_open & 0xFF,
                start_bri & 0xFF,
                close_time & 0xFF,
                cur_time & 0xFF,
                default_light & 0xFF,
                (rgb >> 16) & 0xFF,
                (rgb >> 8) & 0xFF,
                rgb & 0xFF,
                (kelvin >> 8) & 0xFF,
                kelvin & 0xFF,
                fan_open & 0xFF,
                fan_exe_open & 0xFF,
                fan_close_time & 0xFF,
                fan_close_cur_time & 0xFF,
                fan_gear & 0xFF,
            ]
        )
    )


def build_sleep_h6093(
    enable: int,
    start_bri: int,
    close_time: int,
    cur_time: int,
    open_voice: bool,
    voice_code: int,
    voice_volume: int,
    default_light: int,
    rgb: int,
    laser: bool,
) -> bytes:
    """0x33 11 TimerConfig sleep v4: V0 + [openVoice, voiceCode u8, voiceVolume, defaultLight, R, G, B,
    laserSwitch] (kt ControllerSleep.Companion.d:70-75).
    """
    return append_bcc(
        bytes(
            [
                0x33,
                0x11,
                enable & 0xFF,
                start_bri & 0xFF,
                close_time & 0xFF,
                cur_time & 0xFF,
                1 if open_voice else 0,
                voice_code & 0xFF,
                voice_volume & 0xFF,
                default_light & 0xFF,
                (rgb >> 16) & 0xFF,
                (rgb >> 8) & 0xFF,
                rgb & 0xFF,
                1 if laser else 0,
            ]
        )
    )


def build_sleep_h60a0(
    enable: int, start_bri: int, close_time: int, cur_time: int, default_light: int, rgb: int
) -> bytes:
    """0x33 11 H60A0-family colour sleep, no kelvin bytes: V0 + [defaultLight, R, G, B]
    (SleepWithColorController.Companion.b:41-45).
    """
    return append_bcc(
        bytes(
            [
                0x33,
                0x11,
                enable & 0xFF,
                start_bri & 0xFF,
                close_time & 0xFF,
                cur_time & 0xFF,
                default_light & 0xFF,
                (rgb >> 16) & 0xFF,
                (rgb >> 8) & 0xFF,
                rgb & 0xFF,
            ]
        )
    )


def build_sleep_h6630(
    enable: int,
    start_bri: int,
    close_time: int,
    cur_time: int,
    open_effect: bool,
    effect_type: int,
    effect_code: int,
    open_voice: bool,
    voice_volume: int,
    voice_code: int,
    version: int,
) -> bytes:
    """0x33 11 TimerConfig sleep v5: V0 + [openLampEffect, lampEffectType, effectCode u16 LE, openVoice,
    voiceVolume, voiceCode u32 LE, version] (kt ControllerSleep.Companion.e:78-85).
    """
    return append_bcc(
        bytes(
            [
                0x33,
                0x11,
                enable & 0xFF,
                start_bri & 0xFF,
                close_time & 0xFF,
                cur_time & 0xFF,
                1 if open_effect else 0,
                effect_type & 0xFF,
                effect_code & 0xFF,
                (effect_code >> 8) & 0xFF,
                1 if open_voice else 0,
                voice_volume & 0xFF,
            ]
        )
        + (voice_code & 0xFFFFFFFF).to_bytes(4, "little")
        + bytes([version & 0xFF])
    )


def build_sleep_v1(
    enable: int,
    start_bri: int,
    close_time: int,
    cur_time: int,
    open_voice: bool,
    voice_code: int,
    voice_volume: int,
    default_light: int,
    rgb: int,
) -> bytes:
    """0x33 11 V1 sleep: V0 + [openVoice, voiceCode u8, voiceVolume, defaultLight, R, G, B]
    (SleepControllerV1.q:43-51).
    """
    return append_bcc(
        bytes(
            [
                0x33,
                0x11,
                enable & 0xFF,
                start_bri & 0xFF,
                close_time & 0xFF,
                cur_time & 0xFF,
                1 if open_voice else 0,
                voice_code & 0xFF,
                voice_volume & 0xFF,
                default_light & 0xFF,
                (rgb >> 16) & 0xFF,
                (rgb >> 8) & 0xFF,
                rgb & 0xFF,
            ]
        )
    )


def build_sleep_v2(
    enable: int,
    start_bri: int,
    close_time: int,
    cur_time: int,
    default_light: int,
    rgb: int,
    kelvin: int,
    open_voice: bool,
    voice_volume: int,
    voice_code: int,
) -> bytes:
    """0x33 11 V2 sleep: V0 + [defaultLight, R, G, B, kelvin u16 BE, openVoice, voiceVolume, voiceCode u16 LE]
    (SleepControllerV2.q:80-93; ControllerOnlineSleep.Companion.b:53-62).
    """
    return append_bcc(
        bytes(
            [
                0x33,
                0x11,
                enable & 0xFF,
                start_bri & 0xFF,
                close_time & 0xFF,
                cur_time & 0xFF,
                default_light & 0xFF,
                (rgb >> 16) & 0xFF,
                (rgb >> 8) & 0xFF,
                rgb & 0xFF,
                (kelvin >> 8) & 0xFF,
                kelvin & 0xFF,
                1 if open_voice else 0,
                voice_volume & 0xFF,
                voice_code & 0xFF,
                (voice_code >> 8) & 0xFF,
            ]
        )
    )


def read_sleep() -> bytes:
    """0xAA 11, no selector (SleepController():13-15; kt ControllerSleep():117-119;
    SleepWithColorController.Companion.a:35-37; ControllerOnlineSleep.Companion.a:45-49).
    """
    return read_request(0x11)


def build_wakeup(enable: int, end_bri: int, hour: int, minute: int, repeat: int, wake_time: int) -> bytes:
    """0x33 12 V0 wake-up [enable, endBri, hour, minute, repeat, wakeTime] (WakeUpController.q:70-72; kt
    ControllerWakeUp(int..):123-126 via Companion.h:113-115 = TimerConfig wake-up version 0).
    """
    return append_bcc(
        bytes(
            [
                0x33,
                0x12,
                enable & 0xFF,
                end_bri & 0xFF,
                hour & 0xFF,
                minute & 0xFF,
                repeat & 0xFF,
                wake_time & 0xFF,
            ]
        )
    )


def build_wakeup_color(
    enable: int,
    end_bri: int,
    hour: int,
    minute: int,
    repeat: int,
    wake_time: int,
    default_light: int,
    rgb: int,
    kelvin: int = 0,
    *,
    kelvin_be: bool = True,
) -> bytes:
    """0x33 12 colour wake-up: V0 + [defaultLight, R, G, B, kelvin u16 BE (TimerConfig v1) or LE (v3)] (kt
    ControllerWakeUp.Companion.a:44-57; TimerConfig.k:250,254).
    """
    k = (
        bytes([(kelvin >> 8) & 0xFF, kelvin & 0xFF])
        if kelvin_be
        else bytes([kelvin & 0xFF, (kelvin >> 8) & 0xFF])
    )
    return append_bcc(
        bytes(
            [
                0x33,
                0x12,
                enable & 0xFF,
                end_bri & 0xFF,
                hour & 0xFF,
                minute & 0xFF,
                repeat & 0xFF,
                wake_time & 0xFF,
                default_light & 0xFF,
                (rgb >> 16) & 0xFF,
                (rgb >> 8) & 0xFF,
                rgb & 0xFF,
            ]
        )
        + k
    )


def build_wakeup_effect(
    enable: int,
    end_bri: int,
    hour: int,
    minute: int,
    repeat: int,
    wake_time: int,
    open_effect: bool,
    effect_type: int,
    effect_code: int,
) -> bytes:
    """0x33 12 TimerConfig wake-up v6: V0 + [openLampEffect, lampEffectType, effectCode u16 LE] (kt
    ControllerWakeUp.Companion.c:68-73).
    """
    return append_bcc(
        bytes(
            [
                0x33,
                0x12,
                enable & 0xFF,
                end_bri & 0xFF,
                hour & 0xFF,
                minute & 0xFF,
                repeat & 0xFF,
                wake_time & 0xFF,
                1 if open_effect else 0,
                effect_type & 0xFF,
                effect_code & 0xFF,
                (effect_code >> 8) & 0xFF,
            ]
        )
    )


def build_wakeup_effect_lc_music(
    enable: int,
    end_bri: int,
    hour: int,
    minute: int,
    repeat: int,
    wake_time: int,
    open_effect: bool,
    effect_type: int,
    effect_code: int,
    open_voice: bool,
    voice_volume: int,
    voice_code: int,
) -> bytes:
    """0x33 12 TimerConfig wake-up v9: V0 + [openLampEffect, lampEffectType, effectCode u16 LE, openVoice,
    voiceVolume, voiceCode u8] (kt ControllerWakeUp.Companion.b:60-65).
    """
    return append_bcc(
        bytes(
            [
                0x33,
                0x12,
                enable & 0xFF,
                end_bri & 0xFF,
                hour & 0xFF,
                minute & 0xFF,
                repeat & 0xFF,
                wake_time & 0xFF,
                1 if open_effect else 0,
                effect_type & 0xFF,
                effect_code & 0xFF,
                (effect_code >> 8) & 0xFF,
                1 if open_voice else 0,
                voice_volume & 0xFF,
                voice_code & 0xFF,
            ]
        )
    )


def build_wakeup_fanlight(
    enable: int,
    light_open: int,
    end_bri: int,
    hour: int,
    minute: int,
    repeat: int,
    wake_time: int,
    default_light: int,
    rgb: int,
    kelvin: int,
    fan_open: int,
    fan_exe_open: int,
    fan_gear: int,
) -> bytes:
    """0x33 12 TimerConfig wake-up v7 (goodsType 359/368): [enable, lightOpen, endBri, hour, minute, repeat,
    wakeTime, defaultLight, R, G, B, kelvin u16 BE, fanOpen, fanExeOpen, fanGear] (kt
    ControllerWakeUp.Companion.d:76-84).
    """
    return append_bcc(
        bytes(
            [
                0x33,
                0x12,
                enable & 0xFF,
                light_open & 0xFF,
                end_bri & 0xFF,
                hour & 0xFF,
                minute & 0xFF,
                repeat & 0xFF,
                wake_time & 0xFF,
                default_light & 0xFF,
                (rgb >> 16) & 0xFF,
                (rgb >> 8) & 0xFF,
                rgb & 0xFF,
                (kelvin >> 8) & 0xFF,
                kelvin & 0xFF,
                fan_open & 0xFF,
                fan_exe_open & 0xFF,
                fan_gear & 0xFF,
            ]
        )
    )


def build_wakeup_h6093(
    enable: int,
    end_bri: int,
    hour: int,
    minute: int,
    repeat: int,
    wake_time: int,
    open_voice: bool,
    voice_code: int,
    voice_volume: int,
    default_light: int,
    rgb: int,
    laser: bool,
) -> bytes:
    """0x33 12 TimerConfig wake-up v4/v8: V0 + [openVoice, voiceCode u8, voiceVolume, defaultLight, R, G, B,
    laserSwitch] (kt ControllerWakeUp.Companion.e:87-94).
    """
    return append_bcc(
        bytes(
            [
                0x33,
                0x12,
                enable & 0xFF,
                end_bri & 0xFF,
                hour & 0xFF,
                minute & 0xFF,
                repeat & 0xFF,
                wake_time & 0xFF,
                1 if open_voice else 0,
                voice_code & 0xFF,
                voice_volume & 0xFF,
                default_light & 0xFF,
                (rgb >> 16) & 0xFF,
                (rgb >> 8) & 0xFF,
                rgb & 0xFF,
                1 if laser else 0,
            ]
        )
    )


def build_wakeup_h60a0(
    enable: int,
    end_bri: int,
    hour: int,
    minute: int,
    repeat: int,
    wake_time: int,
    default_light: int,
    rgb: int,
) -> bytes:
    """0x33 12 H60A0-family colour wake-up, no kelvin bytes: V0 + [defaultLight, R, G, B]
    (WakeUpWithColorController.Companion.b:41-45).
    """
    return append_bcc(
        bytes(
            [
                0x33,
                0x12,
                enable & 0xFF,
                end_bri & 0xFF,
                hour & 0xFF,
                minute & 0xFF,
                repeat & 0xFF,
                wake_time & 0xFF,
                default_light & 0xFF,
                (rgb >> 16) & 0xFF,
                (rgb >> 8) & 0xFF,
                rgb & 0xFF,
            ]
        )
    )


def build_wakeup_h6630(
    enable: int,
    end_bri: int,
    hour: int,
    minute: int,
    repeat: int,
    wake_time: int,
    open_effect: bool,
    effect_type: int,
    effect_code: int,
    open_voice: bool,
    voice_volume: int,
    voice_code: int,
    version: int,
) -> bytes:
    """0x33 12 TimerConfig wake-up v5: V0 + [openLampEffect, lampEffectType, effectCode u16 LE, openVoice,
    voiceVolume, voiceCode u32 LE, version] (kt ControllerWakeUp.Companion.f:97-104).
    """
    return append_bcc(
        bytes(
            [
                0x33,
                0x12,
                enable & 0xFF,
                end_bri & 0xFF,
                hour & 0xFF,
                minute & 0xFF,
                repeat & 0xFF,
                wake_time & 0xFF,
                1 if open_effect else 0,
                effect_type & 0xFF,
                effect_code & 0xFF,
                (effect_code >> 8) & 0xFF,
                1 if open_voice else 0,
                voice_volume & 0xFF,
            ]
        )
        + (voice_code & 0xFFFFFFFF).to_bytes(4, "little")
        + bytes([version & 0xFF])
    )


def build_wakeup_v1(
    enable: int,
    end_bri: int,
    hour: int,
    minute: int,
    repeat: int,
    wake_time: int,
    open_voice: bool,
    voice_code: int,
    voice_volume: int,
    default_light: int,
    rgb: int,
) -> bytes:
    """0x33 12 V1 wake-up: V0 + [openVoice, voiceCode u8, voiceVolume, defaultLight, R, G, B]
    (WakeUpControllerV1.q:50-58).
    """
    return append_bcc(
        bytes(
            [
                0x33,
                0x12,
                enable & 0xFF,
                end_bri & 0xFF,
                hour & 0xFF,
                minute & 0xFF,
                repeat & 0xFF,
                wake_time & 0xFF,
                1 if open_voice else 0,
                voice_code & 0xFF,
                voice_volume & 0xFF,
                default_light & 0xFF,
                (rgb >> 16) & 0xFF,
                (rgb >> 8) & 0xFF,
                rgb & 0xFF,
            ]
        )
    )


def build_wakeup_v2(
    enable: int,
    end_bri: int,
    hour: int,
    minute: int,
    repeat: int,
    wake_time: int,
    default_light: int,
    rgb: int,
    kelvin: int,
    open_voice: bool,
    voice_volume: int,
    voice_code: int,
) -> bytes:
    """0x33 12 V2 wake-up: V0 + [defaultLight, R, G, B, kelvin u16 BE, openVoice, voiceVolume, voiceCode u16
    LE] (WakeUpControllerV2.q:84-97; ControllerOnlineWakeUp.Companion.b:46-55).
    """
    return append_bcc(
        bytes(
            [
                0x33,
                0x12,
                enable & 0xFF,
                end_bri & 0xFF,
                hour & 0xFF,
                minute & 0xFF,
                repeat & 0xFF,
                wake_time & 0xFF,
                default_light & 0xFF,
                (rgb >> 16) & 0xFF,
                (rgb >> 8) & 0xFF,
                rgb & 0xFF,
                (kelvin >> 8) & 0xFF,
                kelvin & 0xFF,
                1 if open_voice else 0,
                voice_volume & 0xFF,
                voice_code & 0xFF,
                (voice_code >> 8) & 0xFF,
            ]
        )
    )


def read_plug_timer_count(index: int) -> bytes:
    """0xAA 12 [index] H5080-family plug timer-count read; reply [index, count] (h5080
    NewTimerCountController.p:23-25, getCommandType:19-21 = BleConstants.SINGLE_NEW_TIMER_COUNT).
    """
    return read_request(0x12, index)


def read_wakeup() -> bytes:
    """0xAA 12, no selector (WakeUpController():15-17; kt ControllerWakeUp():119-121;
    WakeUpWithColorController.Companion.a:35-37; ControllerOnlineWakeUp.Companion.a:39-41).
    """
    return read_request(0x12)


def build_direction_h6104(direction: int) -> bytes:
    """H6104 video strip direction [direction u8] (DirectionController(int) / q
    split_rgblight/com/govee/h6104/ble/DirectionController.java:11-15,36-37; caller
    split_rgblight/com/govee/h6104/adjust/AdjustAc.java:669).
    """
    return append_bcc(bytes([0x33, 0x13, direction & 0xFF]))


def build_night_mode(brightness: int, rgb: tuple[int, int, int] | None = None) -> bytes:
    """0x33 13 night mode [bri, 0] default colour or [bri, 1, R, G, B] (NightModeViewModel.P,
    base-classes10/com/govee/base2light/ac/nightmode/NightModeViewModel.java:190-215; caller
    base-classes10/com/govee/base2light/ac/nightmode/NightModeAc.java:327).
    """
    if rgb is None:
        return append_bcc(bytes([0x33, 0x13, brightness & 0xFF, 0x00]))
    r, g, b = rgb
    return append_bcc(bytes([0x33, 0x13, brightness & 0xFF, 0x01, r & 0xFF, g & 0xFF, b & 0xFF]))


def build_plug_timer_v2(
    outlet: int, slot: int, enable: bool, on: bool, hour: int, minute: int, repeat: int
) -> bytes:
    """Plug timer V2 write [outlet, slot, enable<<7 | on, hour, minute, repeat] (NewTimerControllerV2.q
    split_plugnew/com/govee/h5080/ble/controller/NewTimerControllerV2.java:100-102; enableAndSwitch from
    TimerInfo base-classes12/com/govee/base_h71xx/sku_base/adjust/component/bean/TimerInfo.java:99-101).
    """
    return append_bcc(
        bytes(
            [
                0x33,
                0x13,
                outlet & 0xFF,
                slot & 0xFF,
                (0x80 if enable else 0x00) | (0x01 if on else 0x00),
                hour & 0xFF,
                minute & 0xFF,
                repeat & 0xFF,
            ]
        )
    )


def read_night_mode() -> bytes:
    """0xAA 13 night-mode read, no selector (bulblightv3 NewDetailVm.f5,
    split_bulblightstringv1/com/govee/bulblightv3/newdetail/NewDetailVm.java:773-781).
    """
    return read_request(0x13)


def read_night_mode_or_direction() -> bytes:
    """Read 0x13 with no selector: night mode on bulblightv3 H601E/F, strip direction on H6104 (NewDetailVm.f5
    Controller4ExtBytes.c with [0x13]
    split_bulblightstringv1/com/govee/bulblightv3/newdetail/NewDetailVm.java:773-781;
    DirectionController(boolean) split_rgblight/com/govee/h6104/ble/DirectionController.java:17-20).
    """
    return read_request(0x13)


def read_plug_timer_h5089(outlet: int, package_index: int) -> bytes:
    """H5089 plug timer read with a packed selector aa 13 <(outlet<<4)|pkg> (Controller4H5089TimerInfo.p
    split_plugnew/com/govee/h5080/ble/controller/h5089/Controller4H5089TimerInfo.java:44-46; caller
    split_plugnew/com/govee/h5080/adjust/h5089/BleOp4H5089.java:618).
    """
    return read_request(0x13, ((outlet << 4) | package_index) & 0xFF)


def read_plug_timer_v2(outlet: int, package_index: int) -> bytes:
    """Plug timer V2 read, aa 13 <outlet> <package 0..3> (NewTimerControllerV2.p
    split_plugnew/com/govee/h5080/ble/controller/NewTimerControllerV2.java:91-93; caller
    split_plugnew/com/govee/h5080/adjust/v1/BleOpV1.java:497).
    """
    return read_request(0x13, outlet, package_index)


def build_gradual(on: bool) -> bytes:
    """33 A3 <1|0> gradual on/off, GradualVersion 0.
    base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Bytes.java:564-566 (Bytes.w) via
    split_rgbiclight/com/govee/rgbiclight/mode/color/ColorControllerConfig.java:507-519,556-560 (i/l).
    """
    return append_bcc(bytes([0x33, 0xA3, 0x01 if on else 0x00]))


def build_gradual_ble(on: bool) -> bytes:
    """33 14 <1|0> gradual on/off, GradualVersion 1.
    base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Bytes.java:558-560 (Bytes.v) via
    Config.java:5608-5612 (M[1] = hK, :14343-14345) and Config.java:322-350 (Companion.I, goodsType-0 SKU
    switch).
    """
    return append_bcc(bytes([0x33, 0x14, 0x01 if on else 0x00]))


def build_night_mode_page(enter: bool) -> bytes:
    """33 14 01 when the night-mode page opens, 33 14 00 when it closes (NightModeViewModel.C/E
    base-classes10/com/govee/base2light/ac/nightmode/NightModeViewModel.java:86-114, from NightModeAc.P0/R0
    base-classes10/com/govee/base2light/ac/nightmode/NightModeAc.java:370-387).
    """
    return append_bcc(bytes([0x33, 0x14, 0x01 if enter else 0x00]))


def build_night_mode_page_open_h601e() -> list[bytes]:
    """goodsType 311 night-mode page open: 33 14 01, then 33 36 01 01 and 33 36 00 00
    ("openSideAndCloseCenter") (NightModeAc.P0/Q0
    base-classes10/com/govee/base2light/ac/nightmode/NightModeAc.java:136-143,370-377;
    NightModeViewModel.C/L
    base-classes10/com/govee/base2light/ac/nightmode/NightModeViewModel.java:86-99,139-158).
    """
    return [
        append_bcc(bytes([0x33, 0x14, 0x01])),
        append_bcc(bytes([0x33, 0x36, 0x01, 0x01])),
        append_bcc(bytes([0x33, 0x36, 0x00, 0x00])),
    ]


def read_wifi_mac_or_gradual() -> bytes:
    """AA 14, no selector: Wi-Fi MAC read on goodsType 21 and the gt-64 bulbs (split_bulblightstringv1
    bulblight ControllerStrategyConfig.h0:648-654); reply frame[2..7] = MAC in display order (firmware:
    H6006 ble_msg_get_wifi_mac_address 0x9b03ca0c, H6008 0x9b03c844, forward decoder).
    base-classes11/com/govee/base2light/kt/general_controller/ControllerWifiMacAddress.java:27-30,36-38;
    split_rgbiclight/com/govee/rgbiclight/ControllerStrategyConfig.java:1328-1334 (V1), :587-589 (g2).
    """
    return read_request(0x14)


def build_h5080_timer_delete(index: int, group: int = 0) -> bytes:
    """0x33 15 [group, index] plug timer delete (TimerDeleteController.q,
    split_plugnew/com/govee/h5080/ble/controller/TimerDeleteController.java:10-30; callers
    split_plugnew/com/govee/h5080/adjust/v2/UiV2.java:927,
    split_plugnew/com/govee/h5080/adjust/v3/UiV3.java:766,
    split_plugnew/com/govee/h5080/adjust/v4_h5160/UiV4.java:804).
    """
    return append_bcc(bytes([0x33, 0x15, group & 0xFF, index & 0xFF]))


def build_h5086_timer_delete(index: int) -> bytes:
    """0x33 15 [index] H5086 timer delete (TimerDeleteController.Companion.a,
    split_plugnew/com/govee/h5086/ble/TimerDeleteController.java:22-36).
    """
    return append_bcc(bytes([0x33, 0x15, index & 0xFF]))


def build_plug_timer_delete(outlet: int, timer_index: int) -> bytes:
    """33 15 [outlet, timer_index] (TimerDeleteController.q
    split_plugnew/com/govee/h5080/ble/controller/TimerDeleteController.java:10-18,28-30;
    H5083NewDetailViewModel.B0
    split_plugnew/com/govee/h5080/adjust/newdetail/H5083NewDetailViewModel.java:152-158 = outlet 0).
    """
    return append_bcc(bytes([0x33, 0x15, outlet & 0xFF, timer_index & 0xFF]))


def read_h6104_ip() -> bytes:
    """0xAA 15 H6104 Wi-Fi IP read, no selector, add-device path (IPController,
    split_rgblight/com/govee/h6104/ble/IPController.java:12-24; caller
    split_rgblight/com/govee/h6104/add/calibration/CalibrationWifiActivity.java:336).
    """
    return read_request(0x15)


def build_abs_music_16(music_code: int, sensitivity: int = 99) -> bytes:
    """0x33 05 16 [musicCode u16 LE, sensitivity] abs (library) music (SubModeAbsMusic.getWriteBytes,
    base-classes10/com/govee/base2light/ble/controller/SubModeAbsMusic.java:21,76-80,97-99;
    ByteUtils.getLowHighBytes base-classes12/com/govee/base2light/util/ByteUtils.java:217-219).
    """
    return append_bcc(
        bytes([0x33, 0x05, 0x16, music_code & 0xFF, (music_code >> 8) & 0xFF, sensitivity & 0xFF])
    )


def build_effect_direction(direction: int) -> bytes:
    """0x33 16 <direction> - H61D3 effect direction (ControllerEffectDirection(int) :61-63 via Companion.d
    :51-53, NewSettingActivity :755).
    """
    return append_bcc(bytes([0x33, 0x16, direction & 0xFF]))


def build_energy_saving(on: bool) -> bytes:
    """0x33 16 [on] energy saving (H6057SettingActivity.K4 TAG_SAVE_ENERGY_MODE,
    split_hollowlamp/com/govee/h6057/origin/newDetail/H6057SettingActivity.java:787,815-819; H6058
    ControllerEnergySaving
    split_hollowlamp/com/govee/hollowlamp/newdetails/h6058/ControllerEnergySaving.java:46-47,56-63 via
    split_hollowlamp/com/govee/hollowlamp/newdetails/h6058/H6058NewDetailVm.java:429).
    """
    return append_bcc(bytes([0x33, 0x16, 0x01 if on else 0x00]))


def build_light_indicator(
    enable: bool,
    start_hour: int = 0,
    start_min: int = 0,
    end_hour: int = 23,
    end_min: int = 59,
    *,
    forever: bool = False,
) -> bytes:
    """0x33 16 indicator / not-disturb window [en, sH, sM, eH, eM] or [en, FF, FF, FF, FF]
    (NewLightIndicatorController.Companion.d,
    split_hollowlamp/com/govee/tablelampv1/ble/NewLightIndicatorController.java:45-48 via
    split_hollowlamp/com/govee/tablelampv1/newAdjust/H6078NewDetailVM.java:742; H5083NewDetailViewModel.v0
    split_plugnew/com/govee/h5080/adjust/newdetail/H5083NewDetailViewModel.java:230-240;
    NotDisturbController.q
    base-classes12/com/govee/base_h71xx/sku_base/ble/controller/NotDisturbController.java:79-90 via
    split_plugnew/com/govee/h5080/adjust/h5089/H5089OpManager.java:493-496).
    """
    en = 0x01 if enable else 0x00
    if forever:
        return append_bcc(bytes([0x33, 0x16, en, 0xFF, 0xFF, 0xFF, 0xFF]))
    return append_bcc(
        bytes([0x33, 0x16, en, start_hour & 0xFF, start_min & 0xFF, end_hour & 0xFF, end_min & 0xFF])
    )


def build_plug_not_disturb_h5086(
    enable: bool, start_hour: int, start_min: int, end_hour: int, end_min: int, forever: bool
) -> bytes:
    """H5086 indicator / not-disturb window with an explicit forever flag [enable, sH, sM, eH, eM, forever]
    (h5086 LightController.Companion.d
    split_plugnew/com/govee/h5086/ble/controller/LightController.java:53; caller
    split_plugnew/com/govee/h5086/adjust/SmartPlugProAdjustViewModel.java:1136).
    """
    return append_bcc(
        bytes(
            [
                0x33,
                0x16,
                0x01 if enable else 0x00,
                start_hour & 0xFF,
                start_min & 0xFF,
                end_hour & 0xFF,
                end_min & 0xFF,
                0x01 if forever else 0x00,
            ]
        )
    )


def read_light_indicator() -> bytes:
    """0xAA 16 indicator / not-disturb read, no selector (NewLightIndicatorController.Companion.b,
    split_hollowlamp/com/govee/tablelampv1/ble/NewLightIndicatorController.java:32-42 via
    split_hollowlamp/com/govee/tablelampv1/newAdjust/H6078NewDetailVM.java:607;
    split_plugnew/com/govee/h5080/adjust/newdetail/H5083NewDetailRepository.java:328-330).
    """
    return read_request(0x16)


def build_plug_pro_exception_state() -> bytes:
    """0x33 17 01: H5086 exception-state write after clear-chart-data (ExceptionStateController.Companion.d,
    split_plugnew/com/govee/h5086/ble/controller/ExceptionStateController.java:44-47).
    """
    return append_bcc(bytes([0x33, 0x17, 0x01]))


def build_wifi_link_start(start: bool) -> bytes:
    """0x33 17: Wi-Fi link start (1) / stop (0) during provisioning
    (WifiLinkStarController.<init>/getCommandType/q,
    base-classes10/com/govee/base2light/ble/controller/WifiLinkStarController.java:7-9,17-19,22-24).
    """
    return append_bcc(bytes([0x33, 0x17, 0x01 if start else 0x00]))


def read_plug_pro_exception_state() -> bytes:
    """0xAA 17: H5086 exception-state read, reply byte0 == 1 means set
    (ExceptionStateController.Companion.b/c,
    split_plugnew/com/govee/h5086/ble/controller/ExceptionStateController.java:26-42).
    """
    return read_request(0x17)


def read_h5086_electric() -> bytes:
    """0xAA 19 H5086 electricity read, no selector (DeviceElectricController.Companion.b / d,
    split_plugnew/com/govee/h5086/ble/controller/DeviceElectricController.java:33-51,62-64; caller
    split_plugnew/com/govee/h5086/adjust/SmartPlugProAdjustViewModel.java:909).
    """
    return read_request(0x19)


def build_h5086_time_monitor(on: bool, hour: int, minute: int, timer_id: int) -> bytes:
    """0x33 1A [on, hour, minute, id u32 BE] H5086 time monitor (TimeMonitorController.Companion.d,
    split_plugnew/com/govee/h5086/ble/controller/TimeMonitorController.java:47-51,87-89; caller
    split_plugnew/com/govee/h5086/adjust/SmartPlugProAdjustViewModel.java:1085).
    """
    return append_bcc(
        bytes([0x33, 0x1A, 0x01 if on else 0x00, hour & 0xFF, minute & 0xFF])
        + (timer_id & 0xFFFFFFFF).to_bytes(4, "big")
    )


def read_h5086_time_monitor() -> bytes:
    """0xAA 1A H5086 time-monitor read, no selector (TimeMonitorController.Companion.b / e,
    split_plugnew/com/govee/h5086/ble/controller/TimeMonitorController.java:34-45,53-68; caller
    split_plugnew/com/govee/h5086/ble/controller/ReadInfoController.java:69).
    """
    return read_request(0x1A)


def build_h5089_nl_brightness(level: int) -> bytes:
    """0x33 1B 01 02 [level] H5089 night-light brightness (Controller4NlSwitch.q,
    split_plugnew/com/govee/h5080/ble/controller/h5089/Controller4NlSwitch.java:29-32,72-81; caller
    H5089OpManager.B split_plugnew/com/govee/h5080/adjust/h5089/H5089OpManager.java:267-270).
    """
    return append_bcc(bytes([0x33, 0x1B, 0x01, 0x02, level & 0xFF]))


def build_h5089_nl_switch(on: bool) -> bytes:
    """33 1B 01 01 on - H5089 night-light on/off (Controller4NlSwitch.q
    split_plugnew/com/govee/h5080/ble/controller/h5089/Controller4NlSwitch.java:66-71).
    """
    return append_bcc(bytes([0x33, 0x1B, 0x01, 0x01, 0x01 if on else 0x00]))


def build_nl71xx_brightness(level: int) -> bytes:
    """3A 1B 01 02 level - light71xx night-light brightness (BrightnessOnOff.Companion.a
    base-classes12/com/govee/base_h71xx/light71xx/model/BrightnessOnOff.java:32-36;
    BrightnessOnOffController.getProType :44-46).
    """
    return append_bcc(bytes([0x3A, 0x1B, 0x01, 0x02, level & 0xFF]))


def build_nl71xx_switch(on: bool) -> bytes:
    """3A 1B 01 01 on - light71xx night-light on/off (BrightnessOnOffController
    base-classes12/com/govee/base_h71xx/light71xx/controller/BrightnessOnOffController.java:44-46,66-72;
    eco FunctionSwitchLightActionParser.x
    base-classes13/com/govee/eco/executeAction/parser/FunctionSwitchLightActionParser.java:88-89).
    """
    return append_bcc(bytes([0x3A, 0x1B, 0x01, 0x01, 0x01 if on else 0x00]))


def build_nl_mode_color(r: int, g: int, b: int, kelvin: int = 0) -> bytes:
    """3A 1B 05 0D R G B kelvin_u16be - night-light colour / colour-temp (LightRgbController.getProType/q
    base-classes12/com/govee/base_h71xx/light71xx/controller/LightRgbController.java:44-46,66-75;
    LightModeInfo.Companion.a
    base-classes12/com/govee/base_h71xx/light71xx/model/LightModeInfo.java:72-106; ModeColor.d
    base-classes12/com/govee/base_h71xx/light71xx/model/ModeColor.java:96-102).
    """
    return append_bcc(
        bytes([0x3A, 0x1B, 0x05, 0x0D, r & 0xFF, g & 0xFF, b & 0xFF, (kelvin >> 8) & 0xFF, kelvin & 0xFF])
    )


def build_nl_mode_default() -> bytes:
    """3A 1B 05 FC - night-light DEFAULT mode, no data (eco DefaultNightLightActionParser.y
    base-classes13/com/govee/eco/executeAction/parser/DefaultNightLightActionParser.java:87-88;
    LightModeInfo.Companion.a base-classes12/com/govee/base_h71xx/light71xx/model/LightModeInfo.java:92-95;
    T1 = R2.id.l2 = 252 base-classes12/com/govee/base_h71xx/sku_base/BleProtocolConstants.java:216).
    """
    return append_bcc(bytes([0x3A, 0x1B, 0x05, 0xFC]))


def build_nl_mode_diy() -> bytes:
    """3A 1B 05 0A 00 00 - night-light DIY mode frame (LightModeInfo.Companion.a
    base-classes12/com/govee/base_h71xx/light71xx/model/LightModeInfo.java:89-90; ModeDiy.Companion.b
    base-classes12/com/govee/base_h71xx/light71xx/model/ModeDiy.java:45-49; H5089OpManager.L/G
    split_plugnew/com/govee/h5080/adjust/h5089/H5089OpManager.java:210,374).
    """
    return append_bcc(bytes([0x3A, 0x1B, 0x05, 0x0A, 0x00, 0x00]))


def build_nl_mode_scene(scene_id: int) -> bytes:
    """3A 1B 05 13 00 id - night-light scene (LightModeInfo.Companion.a
    base-classes12/com/govee/base_h71xx/light71xx/model/LightModeInfo.java:84-88; ModeScene.c
    base-classes12/com/govee/base_h71xx/light71xx/model/ModeScene.java:70-72; H5089OpManager.H
    split_plugnew/com/govee/h5080/adjust/h5089/H5089OpManager.java:386-398).
    """
    return append_bcc(bytes([0x3A, 0x1B, 0x05, 0x13, 0x00, scene_id & 0xFF]))


def read_nl_mode() -> bytes:
    """AA 1B 05 - night-light mode read (LightRgbController.p
    base-classes12/com/govee/base_h71xx/light71xx/controller/LightRgbController.java:48-51).
    """
    return read_request(0x1B, 0x05)


def read_nl_switch() -> bytes:
    """0xAA 1B 01 night-light read (Controller4NlSwitch.p / parseValidBytes,
    split_plugnew/com/govee/h5080/ble/controller/h5089/Controller4NlSwitch.java:25-27,47-63; connect read
    split_plugnew/com/govee/h5080/adjust/h5089/H5089OpManager.java:303).
    """
    return read_request(0x1B, 0x01)


def build_plug_child_lock(on: bool) -> bytes:
    """0x33 1F [02, on] plug child lock (Controller4ChildLock.q,
    split_plugnew/com/govee/h5080/ble/controller/h5089/Controller4ChildLock.java:24-35,56-59, caller
    H5089OpManager.e0 split_plugnew/com/govee/h5080/adjust/h5089/H5089OpManager.java:498-499; H5086
    ChildLockController.Companion.d
    split_plugnew/com/govee/h5086/ble/controller/ChildLockController.java:46-48).
    """
    return append_bcc(bytes([0x33, 0x1F, 0x02, 0x01 if on else 0x00]))


def read_h5086_child_lock() -> bytes:
    """0xAA 1F H5086 child lock read, no selector (ChildLockController.Companion.b,
    split_plugnew/com/govee/h5086/ble/controller/ChildLockController.java:26-43; caller
    split_plugnew/com/govee/h5086/ble/controller/ReadInfoController.java:113).
    """
    return read_request(0x1F)


def read_h5089_child_lock() -> bytes:
    """0xAA 1F 02 H5089 child lock read (Controller4ChildLock.p / parseValidBytes,
    split_plugnew/com/govee/h5080/ble/controller/h5089/Controller4ChildLock.java:20-22,37-54; connect read
    split_plugnew/com/govee/h5080/adjust/h5089/H5089OpManager.java:302).
    """
    return read_request(0x1F, 0x02)


def read_plug_child_lock() -> bytes:
    """AA 1F 02 - H5089/H5083 child-lock read (Controller4ChildLock.p
    split_plugnew/com/govee/h5080/ble/controller/h5089/Controller4ChildLock.java:37-40;
    H5083NewDetailRepository.necessaryMessages
    split_plugnew/com/govee/h5080/adjust/newdetail/H5083NewDetailRepository.java:329).
    """
    return read_request(0x1F, 0x02)


def build_breath_train_mode_20(start: bool) -> bytes:
    """0x33 05 20 [01 start | 02 end] breath training (BreathTrainRunningAc.J2 and long-press end,
    base-classes11/com/govee/base2light/pact/newdetail/content/breath/BreathTrainRunningAc.java:245-256,404-409).
    """
    return append_bcc(bytes([0x33, 0x05, 0x20, 0x01 if start else 0x02]))


def read_wifi_hard_version() -> bytes:
    """0xAA 20 Wi-Fi module hardware-version read, reply ASCII
    (base-classes10/com/govee/base2light/ble/controller/WifiHardVersionController.java:23; kt
    ControllerWifiHardVersion.java:34; split_h61d3 WifiHardController.java:48; base-classes12 bbq
    ControllerWifiHardVersion.java:24).
    """
    return read_request(0x20)


def build_h5127_sync_triggers(entries: Sequence[tuple[int, int]], extra: int = 0) -> list[bytes]:
    """0x33 21 H5127 trigger-sync burst: header, data frames of up to 5 (kind 1-4, value u16be) entries, end;
    the app sends it only base64-wrapped in an IoT ptReal cmd
    (base-classes12/com/govee/base2light/synctriggers/h5127/ControllerSyncTriggers.java:24;
    LinkageOpManager.java:179-215 g, 226-261 i, 387-393 n).
    """
    n = len(entries)
    pkts = n // 5 + (1 if n % 5 else 0) or 1
    frames = [
        append_bcc(bytes([0x33, 0x21, 0x00, pkts & 0xFF, n & 0xFF, 1 if extra > 0 else 0, extra & 0xFF]))
    ]
    if not entries:
        frames.append(append_bcc(bytes([0x33, 0x21, 0x01] + [0xFF] * 16)))
    else:
        for i in range(pkts):
            body = bytearray([0xFF] * 15)
            pos = 0
            for kind, value in entries[i * 5 : i * 5 + 5]:
                if kind not in (1, 2, 3, 4):
                    break
                body[pos : pos + 3] = bytes([kind, (value >> 8) & 0xFF, value & 0xFF])
                pos += 3
            frames.append(append_bcc(bytes([0x33, 0x21, (i + 1) & 0xFF]) + bytes(body) + b"\x00"))
    frames.append(append_bcc(bytes([0x33, 0x21, 0xFF])))
    return frames


def read_wifi_soft_version() -> bytes:
    """0xAA 21 Wi-Fi software version read, reply ASCII (WifiSoftVersionController,
    base-classes10/com/govee/base2light/ble/controller/WifiSoftVersionController.java:9-11,23-31; caller
    split_rgblight/com/govee/stringlightv2/adjust/v2/BleOpV3.java:127).
    """
    return read_request(0x21)


def read_wifi_dsp_version() -> bytes:
    """0xAA 22 Wi-Fi-side DSP-version read, reply u16le
    (base-classes11/com/govee/base2light/kt/general_controller/ControllerWifiDspVersionInDeviceInfo.java:59-65
    ctor/getCommandType; cmd = feast BleProtocol.music_code_banlan = 34,
    base-classes11/com/govee/base2light/pact/feast/BleProtocol.java:5). UNREFERENCED: no call site in the
    decompiled app.
    """
    return read_request(0x22)


def build_new_timer(group: int, enable: bool, turn_on: bool, hour: int, minute: int, repeat: int) -> bytes:
    """0x33 23 [group, enableAndType (bit7 enable, bit0 1=turn on), hour, minute, repeat]
    (NewTimerV1Controller.q:118-122; kt ControllerNewTimer:93-97; H60A0 TimerController.Companion.c:38-41).
    """
    return append_bcc(
        bytes(
            [
                0x33,
                0x23,
                group & 0xFF,
                (0x80 if enable else 0x00) | (0x01 if turn_on else 0x00),
                hour & 0xFF,
                minute & 0xFF,
                repeat & 0xFF,
            ]
        )
    )


def read_new_timer(group: int = 0xFF) -> bytes:
    """0xAA 23 [group]; 0xFF reads all 4 slots (NewTimerV1Controller.p:83-85; kt
    ControllerNewTimer.Companion.a:35-37; H60A0 TimerController.Companion.a:26-28).
    """
    return read_request(0x23, group)


def build_h682x_unbind() -> bytes:
    """0x33 24 01 device unbind (H682xDeviceUnbindController.q:28-30)."""
    return append_bcc(bytes([0x33, 0x24, 0x01]))


def build_ic_points_h61d3(points: Sequence[tuple[int, int]]) -> list[bytes]:
    """0xA3 V1 multi write, comm 0x24: IC point list [count u16be][(x u16be, y u16be)...] for recognition
    setup (split_h61d3/com/govee/h61d3/adjust/newdetail/VM4LightH61d3.java:1290-1303;
    split_h61d3/com/govee/h61d3/ble/controller/MultiController.java:15-21;
    split_h61d3/com/govee/h61d3/pact/H61D3Support.java:1211-1220;
    base-classes10/com/govee/base2light/ble/comm/MultipleControllerCommV1.java:715-783).
    """
    v = bytearray([(len(points) >> 8) & 0xFF, len(points) & 0xFF])
    for x, y in points:
        v += bytes([(x >> 8) & 0xFF, x & 0xFF, (y >> 8) & 0xFF, y & 0xFF])
    value = bytes(v)
    if len(value) <= 14:
        return [append_bcc(bytes([0xA3, 0x00, 0x01, 0x02, 0x24]) + value), append_bcc(bytes([0xA3, 0xFF]))]
    rest = value[14:]
    n = len(rest) // 17 + (1 if len(rest) % 17 else 0)
    frames = [append_bcc(bytes([0xA3, 0x00, 0x01, (n + 1) & 0xFF, 0x24]) + value[:14])]
    for i in range(1, n + 1):
        frames.append(append_bcc(bytes([0xA3, 0xFF if i == n else i & 0xFF]) + rest[(i - 1) * 17 : i * 17]))
    return frames


def build_set_light_start(index: int, time: int) -> bytes:
    """0x33 24 [max(index,1), time u2be] initial light effect at connect (SetLightStartController ctor:12-16,
    q:35-39).
    """
    index = max(index, 1)
    return append_bcc(bytes([0x33, 0x24, index & 0xFF, (time >> 8) & 0xFF, time & 0xFF]))


def read_ic_point() -> bytes:
    """AA 24, no selector; reply frame[2] == 1 means IC points are set (recognition state)
    (split_h61d3/com/govee/h61d3/ble/controller/ReadIcPointController.java:30-47,55-58,64-66; caller
    split_h61d3/com/govee/h61d3/adjust/newdetail/VM4LightH61d3$connectBleSuc$1.java:89-93).
    """
    return read_request(0x24)


def read_preset_info() -> bytes:
    """AA 24: H6057 preset-count read, no selector (orphaned in 7.7.02).
    split_hollowlamp/com/govee/h6057/origin/ble/PreSetInfoController.java:68
    (PreSetInfoController.getCommandType).
    """
    return read_request(0x24)


def build_set_light_end() -> bytes:
    """0x33 25 01: 'light end' write sent once after BLE connect on bulbs (VM4Light.i5).
    split_bulblightstringv1/com/govee/bulblightstringv1/bulblight/VM4Light.java:966-983 (i5: payload [01],
    getCommandType = 37); base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:239
    (value_set_light_end = 37).
    """
    return append_bcc(bytes([0x33, 0x25, 0x01]))


def read_preset_color(page: int) -> bytes:
    """AA 25 <page>: H6057 colour-preset page read, 1-based, <=4 records (orphaned in 7.7.02).
    split_hollowlamp/com/govee/h6057/origin/ble/PreSetColorController.java:62,66-68
    (PreSetColorController.getCommandType/p).
    """
    return read_request(0x25, page)


def build_color_tem_type(tem_type: int) -> bytes:
    """0x33 26 [tem_type] - colour-temperature setting type: 1 "brightest ever", 2 "follow sub switch" (the
    app's analytics labels for the two choices) (Controller4ColorTemTypeSetting.a
    split_bulblightstringv1/com/govee/bulblightv3/newdetail/controller/Controller4ColorTemTypeSetting.java:30-33,65-71;
    SettingAcNewDetail.java:122-128,426,442).
    """
    return append_bcc(bytes([0x33, 0x26, tem_type & 0xFF]))


def build_take_photo_start() -> bytes:
    """0x33 26 01 64 FF FF 00 - enter camera photo light (h61d3 TakePhotoController.a
    split_h61d3/com/govee/h61d3/ble/controller/TakePhotoController.java:27-34,47-48).
    """
    return append_bcc(bytes([0x33, 0x26, 0x01, 0x64, 0xFF, 0xFF, 0x00]))


def build_take_photo_stop() -> bytes:
    """0x33 26 00 - exit camera photo light (h61d3 TakePhotoController.b
    split_h61d3/com/govee/h61d3/ble/controller/TakePhotoController.java:37-39).
    """
    return append_bcc(bytes([0x33, 0x26, 0x00]))


def read_color_tem_type() -> bytes:
    """0xAA 26 - colour-temperature display type read (Controller4ColorTemTypeSetting.b
    split_bulblightstringv1/com/govee/bulblightv3/newdetail/controller/Controller4ColorTemTypeSetting.java:37-40;
    NewDetailVm$connectBleSuc$1.java:146-147).
    """
    return read_request(0x26)


def read_h6057_preset_scene_page(page: int) -> bytes:
    """0xAA 26 [page] - H6057 preset-scene page read (PreSetSceneController.p
    split_hollowlamp/com/govee/h6057/origin/ble/PreSetSceneController.java:17-20,65-70; no caller in
    decomp).
    """
    return read_request(0x26, page)


def read_preset_scene(page: int) -> bytes:
    """AA 26 <page>: H6057 scene-preset page read, 1-based, <=5 records (orphaned in 7.7.02).
    split_hollowlamp/com/govee/h6057/origin/ble/PreSetSceneController.java:65,69-71
    (PreSetSceneController.getCommandType/p).
    """
    return read_request(0x26, page)


def build_h6057_preview_effect(
    effect_type: int,
    brightness: int,
    voice_code: int,
    voice_volume: int,
    default_light: int,
    r: int,
    g: int,
    b: int,
    laser: bool,
) -> bytes:
    """33 27 H6057 sleep(0)/wake-up(1) preview [type][bri][voiceCode, pass 0 when voice is
    off][volume][defaultLight][R][G][B][laser]
    (split_hollowlamp/com/govee/h6057/origin/newDetail/H6057DetailVM.java:1094-1121;
    base-classes11/com/govee/base2light/kt/general_controller/ControllerSingle.java:37-41).
    """
    return append_bcc(
        bytes(
            [
                0x33,
                0x27,
                effect_type & 0xFF,
                brightness & 0xFF,
                voice_code & 0xFF,
                voice_volume & 0xFF,
                default_light & 0xFF,
                r & 0xFF,
                g & 0xFF,
                b & 0xFF,
                1 if laser else 0,
            ]
        )
    )


def build_power_warning(enabled: bool | None, watts: int | None) -> bytes:
    """33 27 - H5086 alarm-power set [open 1/0/FF(None), watts u2be | FFFF].
    split_plugnew/com/govee/h5086/ble/controller/PowerWarningController.java:49-58 (Companion.d).
    """
    o = 0xFF if enabled is None else (1 if enabled else 0)
    v = (0xFF, 0xFF) if watts is None else ((watts >> 8) & 0xFF, watts & 0xFF)
    return append_bcc(bytes([0x33, 0x27, o, *v]))


def build_wash_remind(enabled: bool, wash_hour: int, *, reset: bool = False, pro_type: int = 0x3A) -> bytes:
    """3A 27: H71xx wash-reminder set [2 if reset else open, washHour u16be]; proType = mutable default 0x3A
    (h=true) or 0x33 (h=false); no caller in decomp.
    base-classes12/com/govee/base_h71xx/sku_base/ble/controller/WashRemindController.java:46-51,62-76
    (getProType/q).
    """
    return append_bcc(
        bytes(
            [
                pro_type & 0xFF,
                0x27,
                2 if reset else (1 if enabled else 0),
                (wash_hour >> 8) & 0xFF,
                wash_hour & 0xFF,
            ]
        )
    )


def read_op27() -> bytes:
    """AA 27: no-selector 0x27 read; H5086 power-warning read and (default proType) H71xx wash-reminder read.
    split_plugnew/com/govee/h5086/ble/controller/PowerWarningController.java:37-46 (Companion.b);
    base-classes12/com/govee/base_h71xx/sku_base/ble/controller/WashRemindController.java:23-25,46-51
    (<init>/getProType).
    """
    return read_request(0x27)


def read_power_warning() -> bytes:
    """AA 27 - H5086 power-warning read, no selector.
    split_plugnew/com/govee/h5086/ble/controller/PowerWarningController.java:37-46 (Companion.b).
    """
    return read_request(0x27)


def build_delete_preset_scene(scene_code: int) -> bytes:
    """33 28: H6057 delete one stored scene preset [code u16le] (orphaned in 7.7.02).
    split_hollowlamp/com/govee/h6057/origin/ble/DeletePreSetSceneController.java:21-27 (getCommandType/q);
    split_hollowlamp/com/govee/h6057/view/preset/PresetEditActivity.java:457 (PresetEditActivity.L1).
    """
    return append_bcc(bytes([0x33, 0x28, scene_code & 0xFF, (scene_code >> 8) & 0xFF]))


def build_h5089_sleep(
    enable: int,
    start_bri: int,
    close_time: int,
    cur_time: int,
    default_light: int,
    r: int,
    g: int,
    b: int,
    kelvin: int = 0,
) -> bytes:
    """33 28 - H5089 night-light sleep [en, startBri, closeTime, curTime, defaultLight, R, G, B, kelvin u16le]
    (Controller4NlSleep.q
    split_plugnew/com/govee/h5080/ble/controller/h5089/Controller4NlSleep.java:53-57,63-65,79-86).
    """
    return append_bcc(
        bytes(
            [
                0x33,
                0x28,
                enable & 0xFF,
                start_bri & 0xFF,
                close_time & 0xFF,
                cur_time & 0xFF,
                default_light & 0xFF,
                r & 0xFF,
                g & 0xFF,
                b & 0xFF,
                kelvin & 0xFF,
                (kelvin >> 8) & 0xFF,
            ]
        )
    )


def build_power_warning_v2(kind: int, enabled: bool, watts: int | None) -> bytes:
    """33 28: H5086 power threshold V2 [kind 1=alarm/2=cut-off, open, watts u16be | FFFF].
    split_plugnew/com/govee/h5086/ble/controller/PowerWarningControllerV2.java:24-33,44 (Companion.a/b,
    getCommandType).
    """
    v = (0xFF, 0xFF) if watts is None else ((watts >> 8) & 0xFF, watts & 0xFF)
    return append_bcc(bytes([0x33, 0x28, kind & 0xFF, 1 if enabled else 0, *v]))


def read_h5089_sleep() -> bytes:
    """AA 28: H5089 night-light sleep read, no selector.
    split_plugnew/com/govee/h5080/ble/controller/h5089/Controller4NlSleep.java:48-51,63
    (<init>/getCommandType); split_plugnew/com/govee/h5080/adjust/h5089/BleOp4H5089.java:160.
    """
    return read_request(0x28)


def build_h5089_wakeup(
    enable: int,
    end_bri: int,
    hour: int,
    minute: int,
    repeat: int,
    wake_time: int,
    default_light: int,
    r: int,
    g: int,
    b: int,
    kelvin: int = 0,
) -> bytes:
    """33 29: H5089 night-light wake-up set (hour/min clamped like WakeUpController.u/v; kelvin u16le only for
    colour-temperature rgb).
    split_plugnew/com/govee/h5080/ble/controller/h5089/Controller4NlWakeUp.java:74,89-96
    (getCommandType/q).
    """
    return append_bcc(
        bytes(
            [
                0x33,
                0x29,
                enable & 0xFF,
                end_bri & 0xFF,
                min(max(hour, 0), 23),
                min(max(minute, 0), 59),
                repeat & 0xFF,
                wake_time & 0xFF,
                default_light & 0xFF,
                r & 0xFF,
                g & 0xFF,
                b & 0xFF,
                kelvin & 0xFF,
                (kelvin >> 8) & 0xFF,
            ]
        )
    )


def build_sort_preset_scenes(scene_codes: Sequence[int]) -> bytes:
    """33 29: H6057 reorder stored scene presets [N, code u16le x N], N<=8 (orphaned in 7.7.02).
    split_hollowlamp/com/govee/h6057/origin/ble/SortPreSetSceneController.java:21-27 (getCommandType/q);
    split_hollowlamp/com/govee/h6057/view/preset/PresetEditActivity.java:492-513 (PresetEditActivity.O1).
    """
    if len(scene_codes) > 8:
        raise ValueError("max 8 scene codes per frame")
    body = [len(scene_codes)]
    for c in scene_codes:
        body += [c & 0xFF, (c >> 8) & 0xFF]
    return append_bcc(bytes([0x33, 0x29, *body]))


def read_h5089_wakeup() -> bytes:
    """AA 29: H5089 night-light wake-up read, no selector.
    split_plugnew/com/govee/h5080/ble/controller/h5089/Controller4NlWakeUp.java:59-62,74
    (<init>/getCommandType); split_plugnew/com/govee/h5080/adjust/h5089/BleOp4H5089.java:159.
    """
    return read_request(0x29)


def build_h6057_light_switch(on: bool) -> bytes:
    """0x33 30 [on]: H6057 main-light switch
    (H6057DetailVM.h5 :956, ControllerSingle.Companion.c(48, [1|0]))."""
    return append_bcc(bytes([0x33, 0x30, 0x01 if on else 0x00]))


def build_light_direction(direction: int) -> bytes:
    """33 30 [direction]: H6038 installation/calibration direction index.
    split_h6047/com/govee/h6047/newdetail/h6038/controller/CalibrationController.java:42-45
    (CalibrationController.Companion.c, Controller4ExtBytes.d write); callers
    split_h6047/com/govee/h6047/newdetail/h6038/VM4H6038.java:809-815 (N5, via H6038CalibrationAc.java:316)
    and split_h6047/com/govee/h6047/newdetail/h6038/H6038CalibrationAc.java:292-298 (Q0, add-device flow).
    """
    return append_bcc(bytes([0x33, 0x30, direction & 0xFF]))


def build_light_direction_exit_calibration(value: int) -> list[bytes]:
    """[33 30 value, 33 44 02 00] - set direction then exit calibration (DefWriteComposeController4Single.g
    base-classes10/com/govee/base2light/ble/controller/compose/DefWriteComposeController4Single.java:137-143;
    compose/CalibrationController.java:30-31,58-59).
    """
    return [append_bcc(bytes([0x33, 0x30, value & 0xFF])), append_bcc(bytes([0x33, 0x44, 0x02, 0x00]))]


def build_light_switch_h6057(on: bool) -> bytes:
    """33 30 [1|0]: H6057 main-light switch.
    split_hollowlamp/com/govee/h6057/origin/newDetail/H6057DetailVM.java:950-962 (h5:
    ControllerSingle.Companion.c(command_light_switch = 48, [1 when the light is off, else 0])).
    """
    return append_bcc(bytes([0x33, 0x30, 0x01 if on else 0x00]))


def build_op30_byte(value: int) -> bytes:
    """0x33 30 [value] - single-byte 0x30 write (compose DirectionController.b
    base-classes10/com/govee/base2light/ble/controller/compose/DirectionController.java:34-35,60-61; H6038
    CalibrationController.c
    split_h6047/com/govee/h6047/newdetail/h6038/controller/CalibrationController.java:43-44; H6057
    split_hollowlamp/com/govee/h6057/origin/newDetail/H6057DetailVM.java:956; H71xx
    ReportChartController.java:24-31).
    """
    return append_bcc(bytes([0x33, 0x30, value & 0xFF]))


def build_zone_power(zone: int, on: bool) -> bytes:
    """33 30 [zone, state]: h60ax per-zone power.
    split_h60ax/com/govee/pact_h60a0/adjust/h60a6/VM4LightH60A6.java:1373-1376 (y5;
    DelegateHeaderH60A6.java:442,470);
    split_h60ax/com/govee/pact_h60a0/adjust/h1270/VM4LightH1270.java:675;
    split_h60ax/com/govee/pact_h60a0/adjust/h1250/VM4LightH1250.java:914;
    split_h60ax/com/govee/pact_h60a0/adjust/h12d0/VM4LightH12D0.java:618-622 (B5;
    DelegateHeaderH12D0.java:580,608,636).
    """
    return append_bcc(bytes([0x33, 0x30, zone & 0xFF, 0x01 if on else 0x00]))


def read_light_direction() -> bytes:
    """AA 30, no selector. Defined but never sent in 7.7.02:
    split_h6047/com/govee/h6047/newdetail/h6038/controller/CalibrationController.java:34-40 (Companion.b,
    no caller);
    base-classes10/com/govee/base2light/ble/controller/compose/DirectionController.java:26-30,60-61;
    base-classes10/com/govee/base2light/ble/controller/LightSwitchH6057Controller.java:9-11,24-25 (never
    constructed).
    """
    return read_request(0x30)


def read_op30() -> bytes:
    """0xAA 30 - 0x30 read, no selector (compose DirectionController.a
    base-classes10/com/govee/base2light/ble/controller/compose/DirectionController.java:28-29; H6038
    CalibrationController.b
    split_h6047/com/govee/h6047/newdetail/h6038/controller/CalibrationController.java:35-39;
    LightSwitchH6057Controller()
    base-classes10/com/govee/base2light/ble/controller/LightSwitchH6057Controller.java:9-11).
    """
    return read_request(0x30)


def build_cube_shape_layout(
    shapes: list[tuple[int, float, float, int]], ext: tuple[int, int] | None = None
) -> list[bytes]:
    """Cube shape layout, 0xA3 V1 comm 0x31, value [n] + n x [type][ceil(x) s16le][ceil(y) s16le][angle s16le]
    (+ [powerNumber][powerEdgeNumber])
    (split_h6061/com/govee/cubelight/ble/MultiDeviceController.java:17-53,59-65,71-73;
    base-classes10/com/govee/base2light/ble/comm/MultipleControllerCommV1.java:715-783,833-843).
    """
    import math

    value: bytes | bytearray = bytearray([len(shapes) & 0xFF])
    for shape_type, x, y, angle in shapes:
        value += bytes([shape_type & 0xFF])
        for v in (math.ceil(x), math.ceil(y), angle):
            value += (v & 0xFFFF).to_bytes(2, "little")
    if ext is not None:
        value += bytes([ext[0] & 0xFF, ext[1] & 0xFF])
    value = bytes(value)
    start = bytearray([0xA3, 0x00, 0x01, 0x00, 0x31])
    mids, last = [], b""
    if len(value) <= 14:
        start += value
        total = 2
    else:
        start += value[:14]
        rest = value[14:]
        k = (len(rest) + 16) // 17
        for i in range(1, k + 1):
            chunk = rest[(i - 1) * 17 : i * 17]
            if i == k:
                last = chunk
            else:
                mids.append(append_bcc(bytes([0xA3, i & 0xFF]) + chunk))
        total = k + 1
    start[3] = total & 0xFF
    return [append_bcc(bytes(start)), *mids, append_bcc(bytes([163, 255]) + last)]


def build_cube_shape_layout_h606a(
    angle: int, points: list[tuple[float, float]], use_round: bool = True
) -> list[bytes]:
    """H606A/H6069 shape layout, 0xA3 V1 comm 0x31, value [angle s16le][n] + n x [x s16le][y s16le],
    Math.round in the 3-arg ctor, ceil in the 2-arg ctor
    (split_h6061/com/govee/cubelight/ble/MultiDeviceControllerH606A.java:20-47,49-76,152-158,164-166;
    base-classes10/com/govee/base2light/ble/comm/MultipleControllerCommV1.java:715-783,833-843).
    """
    import math

    conv = (lambda f: math.floor(f + 0.5)) if use_round else math.ceil
    value: bytes | bytearray = bytearray((angle & 0xFFFF).to_bytes(2, "little") + bytes([len(points) & 0xFF]))
    for x, y in points:
        value += (conv(x) & 0xFFFF).to_bytes(2, "little") + (conv(y) & 0xFFFF).to_bytes(2, "little")
    value = bytes(value)
    start = bytearray([0xA3, 0x00, 0x01, 0x00, 0x31])
    mids, last = [], b""
    if len(value) <= 14:
        start += value
        total = 2
    else:
        start += value[:14]
        rest = value[14:]
        k = (len(rest) + 16) // 17
        for i in range(1, k + 1):
            chunk = rest[(i - 1) * 17 : i * 17]
            if i == k:
                last = chunk
            else:
                mids.append(append_bcc(bytes([0xA3, i & 0xFF]) + chunk))
        total = k + 1
    start[3] = total & 0xFF
    return [append_bcc(bytes(start)), *mids, append_bcc(bytes([163, 255]) + last)]


def build_fanlight_fan_gear(gear: int) -> bytes:
    """0x33 31 01 <gear>: fan-light fan gear (eco FanMorningParamActionParser.y
    base-classes13/com/govee/eco/executeAction/parser/FanMorningParamActionParser.java:110-112 when exeOpen
    == 1; first frame of FanParamActionParser.y :170-179).
    """
    return append_bcc(bytes([0x33, 0x31, 0x01, gear & 0xFF]))


def build_fanlight_fan_param(
    gear: int, order: int, shake: int | None = None, mode: int | None = None
) -> list[bytes]:
    """0x33 31 sub-op frames in app order: [01 gear], [02 order], then [04 shake] (goodsType 368) and [03 01
    00 mode] (goodsType 368 and versionSoft >= '1.00.11') (eco FanParamActionParser.y
    base-classes13/com/govee/eco/executeAction/parser/FanParamActionParser.java:170-179, gate :46-49).
    """
    frames = [
        append_bcc(bytes([0x33, 0x31, 0x01, gear & 0xFF])),
        append_bcc(bytes([0x33, 0x31, 0x02, order & 0xFF])),
    ]
    if shake is not None:
        frames.append(append_bcc(bytes([0x33, 0x31, 0x04, shake & 0xFF])))
    if mode is not None:
        frames.append(append_bcc(bytes([0x33, 0x31, 0x03, 0x01, 0x00, mode & 0xFF])))
    return frames


def build_fanlight_fan_switch(on: bool) -> bytes:
    """0x33 31 00 <open>: fan-light fan on/off (eco FanSwitchActionParser.y
    base-classes13/com/govee/eco/executeAction/parser/FanSwitchActionParser.java:45-47; off form also
    FanMorningParamActionParser.y :110-112).
    """
    return append_bcc(bytes([0x33, 0x31, 0x00, 0x01 if on else 0x00]))


def build_h6057_play_voice(voice: int) -> bytes:
    """0x33 31 [musicCode]: H6057 white-noise play (H6057DetailVM.G5
    split_hollowlamp/com/govee/h6057/origin/newDetail/H6057DetailVM.java:694
    ControllerSingle.c(controller_type_play_voice=49, [code]); unused Controller4PlayVoice.q :41-43).
    """
    return append_bcc(bytes([0x33, 0x31, voice & 0xFF]))


def build_h6067_shape_layout(
    shapes: list[tuple[int, float, float, int, int]], goods_type: int, ext: tuple[int, int] | None = None
) -> list[bytes]:
    """h6067 (H6067/H6065) shape layout, 0xA3 V1 comm 0x31, value [n] + n x [type][ceil(x) s16le][ceil(y)
    s16le][angle s16le] (+ [powerNumber][powerEdgeNumber]) and, for goodsType 121 only, [00 00] + n x
    [state] (split_h6061/com/govee/h6067/ble/MultiDeviceController.java:18-65,75-77,83-85;
    base-classes10/com/govee/base2light/ble/comm/MultipleControllerCommV1.java:715-783,833-843).
    """
    import math

    value: bytes | bytearray = bytearray([len(shapes) & 0xFF])
    for shape_type, x, y, angle, _state in shapes:
        value += bytes([shape_type & 0xFF])
        for v in (math.ceil(x), math.ceil(y), angle):
            value += (v & 0xFFFF).to_bytes(2, "little")
    if ext is not None:
        value += bytes([ext[0] & 0xFF, ext[1] & 0xFF])
    if goods_type == 121:
        value += bytes(2) + bytes([s[4] & 0xFF for s in shapes])
    value = bytes(value)
    start = bytearray([0xA3, 0x00, 0x01, 0x00, 0x31])
    mids, last = [], b""
    if len(value) <= 14:
        start += value
        total = 2
    else:
        start += value[:14]
        rest = value[14:]
        k = (len(rest) + 16) // 17
        for i in range(1, k + 1):
            chunk = rest[(i - 1) * 17 : i * 17]
            if i == k:
                last = chunk
            else:
                mids.append(append_bcc(bytes([0xA3, i & 0xFF]) + chunk))
        total = k + 1
    start[3] = total & 0xFF
    return [append_bcc(bytes(start)), *mids, append_bcc(bytes([163, 255]) + last)]


def build_op31_byte(value: int) -> bytes:
    """0x33 31 [value] - single-byte 0x31 write (striplight LightPosController.q
    split_h6062/com/govee/striplight/ble/LightPosController.java:16-19,42-44;
    split_h6062/com/govee/striplight/newdetail/SettingAc.java:419;
    split_rgbiclight/com/govee/h7090/adjust/newDetail/SettingActivity.java:322; H6057
    split_hollowlamp/com/govee/h6057/origin/newDetail/H6057DetailVM.java:694).
    """
    return append_bcc(bytes([0x33, 0x31, value & 0xFF]))


def build_strip_light_pos(pos: int) -> bytes:
    """0x33 31 [pos], pos 0|1 toggle: light position (Controller4ExtBytes.d([SINGLE_LIGHT_POS=49, pos])
    split_h6062/com/govee/striplight/newdetail/SettingAc.java:418-419 (H610A);
    split_rgbiclight/com/govee/h7090/adjust/newDetail/SettingActivity.java:321-322 (H7095)).
    """
    return append_bcc(bytes([0x33, 0x31, pos & 0xFF]))


def read_op31() -> bytes:
    """0xAA 31 - light-position read (LightPosController()
    split_h6062/com/govee/striplight/ble/LightPosController.java:12-14; kt ControllerLightPos.a
    base-classes11/com/govee/base2light/kt/general_controller/ControllerLightPos.java:31-38,52-53).
    """
    return read_request(0x31)


def read_strip_light_pos() -> bytes:
    """0xAA 31, no selector: light position read (kt ControllerLightPos.makeController4Read, default args
    base-classes11/com/govee/base2light/kt/general_controller/ControllerLightPos.java:31-64; makeH610A /
    makeH7095 strategies). Reply validBytes[0] = pos (signed).
    """
    return read_request(0x31)


def build_h6057_pause_voice() -> bytes:
    """0x33 32 with no payload: H6057 white-noise pause (H6057DetailVM.G5 :694,
    ControllerSingle.Companion.c(0x32) with an empty payload).
    """
    return append_bcc(bytes([0x33, 0x32]))


def build_pause_voice_h6057() -> bytes:
    """0x33 32 with no payload: H6057 white-noise pause (H6057DetailVM
    split_hollowlamp/com/govee/h6057/origin/newDetail/H6057DetailVM.java:694; ControllerSingle.Companion.c
    base-classes11/com/govee/base2light/kt/general_controller/ControllerSingle.java:38-41).
    """
    return append_bcc(bytes([0x33, 0x32]))


def build_pause_voice_h6057_v1(code: int) -> bytes:
    """0x33 32 [code], unused older builder (Controller4PauseVoice.q
    split_hollowlamp/com/govee/h6057/origin/ble/Controller4PauseVoice.java:29-31,37-43); no caller in
    decomp.
    """
    return append_bcc(bytes([0x33, 0x32, code & 0xFF]))


def read_check_camera() -> bytes:
    """0xAA 32 - camera status read (CheckCameraVmInterface.getReadCameraStatusCamera
    base-classes12/com/govee/base2light/videomode/newdetail/funtion/CheckCameraVmInterface.java:57-58;
    base-classes10/com/govee/base2light/ble/controller/CheckCameraController.java:11-13,36-37,51-52;
    CheckCameraInstallController.java:30-31,69-70).
    """
    return read_request(0x32)


def build_lr_switch(first_on: bool, second_on: bool) -> bytes:
    """H6062/H7090 dual sub-strip switch, op = 0x01*first | 0x10*second (SwitchLRController.q
    split_h6062/com/govee/striplight/ble/SwitchLRController.java:63-65; ControllerSwitchLR(write, [op])
    split_h6062/com/govee/striplight/newdetail/VM4Light.java:726-741).
    """
    return append_bcc(bytes([0x33, 0x33, (0x01 if first_on else 0x00) | (0x10 if second_on else 0x00)]))


def build_volume(volume: int) -> bytes:
    """H6057 volume [volume u8] (H6057DetailVM.J5 ControllerSingle.c(command_volume = 51, [volume])
    split_hollowlamp/com/govee/h6057/origin/newDetail/H6057DetailVM.java:714-719).
    """
    return append_bcc(bytes([0x33, 0x33, volume & 0xFF]))


def build_volume_h6057(volume: int) -> bytes:
    """0x33 33 [volume] H6057 volume (H6057DetailVM.J5,
    split_hollowlamp/com/govee/h6057/origin/newDetail/H6057DetailVM.java:714-719; command_volume
    split_hollowlamp/com/govee/h6057/origin/ble/BleProtocol.java:11).
    """
    return append_bcc(bytes([0x33, 0x33, volume & 0xFF]))


def read_lr_switch() -> bytes:
    """H6062/H7090 L/R switch state and heartbeat, aa 33 with no selector (SwitchLRController()
    split_h6062/com/govee/striplight/ble/SwitchLRController.java:14-16; ControllerSwitchLR.Companion.a
    base-classes11/com/govee/base2light/kt/general_controller/ControllerSwitchLR.java:39-51;
    ComposeHeartController split_h6062/com/govee/striplight/ble/ComposeHeartController.java:8-19).
    """
    return read_request(0x33)


def build_swap_light() -> bytes:
    """33 34: H6047/H6048 swap left/right light-bar position, empty payload.
    split_h6047/com/govee/h6047/ble/controller/SwapLightController.java:13-19 (getCommandType/q);
    split_h6047/com/govee/h6047/detail/SettingAcNewDetail.java:324 (o4);
    split_h6047/com/govee/h6047/newdetail/h6048/VM4H6048.java:714 (C5).
    """
    return append_bcc(bytes([0x33, 0x34]))


def read_h6057_detail() -> bytes:
    """AA 34: H6057 detail/status read at connect, no selector.
    split_hollowlamp/com/govee/h6057/origin/newDetail/controller/Controllers.java:63 (Controllers.f);
    split_hollowlamp/com/govee/h6057/origin/newDetail/H6057DetailVM.java:538.
    """
    return read_request(0x34)


def build_without_interrupt(on: bool) -> bytes:
    """0x33 35 [on] H6057 no-disturbance / power-on without interruption (H6057SettingActivity.K4
    TAG_NO_DISTURBANCE,
    split_hollowlamp/com/govee/h6057/origin/newDetail/H6057SettingActivity.java:787,789-793;
    command_no_disturb split_hollowlamp/com/govee/h6057/origin/ble/BleProtocol.java:9).
    """
    return append_bcc(bytes([0x33, 0x35, 0x01 if on else 0x00]))


def build_bar_switch_index(index: int, on: bool) -> bytes:
    """Per-bar / per-zone switch [index, on] (ComposeLightControllerV1.q
    split_h6047/com/govee/h6047/ble/controller/ComposeLightControllerV1.java:32-34; Controller4ExtBytes
    [54, index, on] on H6043/H6038/H6048/H60B2/H1771/H601E-F; pact_h60a0 ControllerChildSwitch.Companion.a
    split_h60ax/com/govee/pact_h60a0/ble/controller/ControllerChildSwitch.java:25-27).
    """
    return append_bcc(bytes([0x33, 0x36, index & 0xFF, 0x01 if on else 0x00]))


def build_bar_switch_pair(left: bool, right: bool) -> bytes:
    """H6047 both-bar switch in one frame [left, right] (ComposeLightController.q
    split_h6047/com/govee/h6047/ble/controller/ComposeLightController.java:34-36; NewDetailVm.h5
    Controller4ExtBytes.e([0x36, left, right]) split_h6047/com/govee/h6047/detail/NewDetailVm.java:298).
    """
    return append_bcc(bytes([0x33, 0x36, 0x01 if left else 0x00, 0x01 if right else 0x00]))


def build_child_switch(is_main: bool, on: bool) -> bytes:
    """0x33 36 [zone 0=main | 1=background][on 1|0]: H60A0 (188) control-screen zone switch (VM4LightH60A0.k5
    split_h60ax/com/govee/pact_h60a0/adjust/h60a0/VM4LightH60A0.java:1023-1033 from
    DelegateHeaderH60A0.m0/j0
    split_h60ax/com/govee/pact_h60a0/adjust/h60a0/DelegateHeaderH60A0.java:420-432,448-460;
    ControllerChildSwitch.Companion.a
    split_h60ax/com/govee/pact_h60a0/ble/controller/ControllerChildSwitch.java:25-27,44-46).
    """
    return append_bcc(bytes([0x33, 0x36, 0x00 if is_main else 0x01, 0x01 if on else 0x00]))


def build_prompt_tone(on: bool) -> bytes:
    """0x33 37 [on] H6057 prompt tone / hint voice (H6057SettingActivity.K4 TAG_HINT_VOICE,
    split_hollowlamp/com/govee/h6057/origin/newDetail/H6057SettingActivity.java:787,802-806;
    command_hint_voice split_hollowlamp/com/govee/h6057/origin/ble/BleProtocol.java:5).
    """
    return append_bcc(bytes([0x33, 0x37, 0x01 if on else 0x00]))


def build_guide_light_h6057(on: bool) -> bytes:
    """0x33 38 [on] H6057 indicator / guide light (H6057SettingActivity.K4 TAG_INDICATOR_LIGHT,
    split_hollowlamp/com/govee/h6057/origin/newDetail/H6057SettingActivity.java:787,828-832;
    command_indicator_light split_hollowlamp/com/govee/h6057/origin/ble/BleProtocol.java:6).
    """
    return append_bcc(bytes([0x33, 0x38, 0x01 if on else 0x00]))


def build_init_light(value: int) -> bytes:
    """0x33 38 [value] init/guide (indicator) light; H6057 sends 1|0 (H6057SettingActivity,
    split_hollowlamp/com/govee/h6057/origin/newDetail/H6057SettingActivity.java:787,830-832;
    InitLightController.q
    base-classes10/com/govee/base2light/ble/controller/InitLightController.java:14-17,43-45;
    GuideLightController.q split_hollowlamp/com/govee/h6057/origin/ble/GuideLightController.java:47-49).
    """
    return append_bcc(bytes([0x33, 0x38, value & 0xFF]))


def build_check_direction(value: int) -> bytes:
    """0x33 39 [value] - direction set (CheckDirectionFinishController.q
    base-classes10/com/govee/base2light/ble/controller/CheckDirectionFinishController.java:14-17,25-26,36-37;
    kt ControllerDirection.b
    base-classes11/com/govee/base2light/kt/general_controller/ControllerDirection.java:31-32,48-49;
    CalibrationController4H6039.b
    split_h6047/com/govee/h6047/newdetail/control/CalibrationController4H6039.java:35-36).
    """
    return append_bcc(bytes([0x33, 0x39, value & 0xFF]))


def read_check_direction() -> bytes:
    """0xAA 39 - direction read (CheckDirectionFinishController()
    base-classes10/com/govee/base2light/ble/controller/CheckDirectionFinishController.java:10-12;
    ControllerDirection.a ControllerDirection.java:26-27; CalibrationController4H6039.a
    split_h6047/com/govee/h6047/newdetail/control/CalibrationController4H6039.java:27-29).
    """
    return read_request(0x39)


def build_splicing_config(count: int, position: int, macs: Sequence[str], cmd: int = 0x40) -> bytes:
    """0x33 40 01 [count, layout, MAC byte-reversed x N]: count = the devices spliced to the main one (the
    splice list less the main device), position = the main device's index in the splice list. Layout byte:
    position 0 -> 0x60 for one sub-device, else 0x6C; position 1 -> 0x90 for one, else 0x9C; any other
    position -> 0xD8 (ControllerChangeSplicing.d:94-108, layout ControllerChangeSplicing.a:71-81; truncated
    to 17 bytes by kt AbsController.q:47-60). count and position:
    base-classes4/com/govee/splicing/splicingUi/DeviceSplicingActivity.java:428-456;
    base-classes4/com/govee/splicing/splicingUi/DeviceSplicingViewModel$exitSplicingModeAndClose$1.java:35-44.
    """
    if position == 0:
        layout = 0x60 if count == 1 else 0x6C
    elif position != 1:
        layout = 0xD8
    else:
        layout = 0x90 if count == 1 else 0x9C
    body = bytes([0x01, count & 0xFF, layout])
    for m in macs:
        body += bytes(int(x, 16) for x in m.split(":"))[::-1]
    return append_bcc(bytes([0x33, cmd & 0xFF]) + body[:17])


def build_splicing_mode(rgb: tuple[int, int, int] | None = None, cmd: int = 0x40) -> bytes:
    """0x33 40 02: enter splicing mode [02, 01, R, G, B] or exit [02, 00] (SplicingController.q:61-69)."""
    if rgb is None:
        return append_bcc(bytes([0x33, cmd & 0xFF, 0x02, 0x00]))
    r, g, b = rgb
    return append_bcc(bytes([0x33, cmd & 0xFF, 0x02, 0x01, r & 0xFF, g & 0xFF, b & 0xFF]))


def build_splicing_release(cmd: int = 0x40) -> bytes:
    """0x33 40 01 FF: H70B1 unbind / release splicing (H70B1DeviceUnbindController.q:28-30;
    ControllerReleaseSplicing.q:57-59; ControllerChangeSplicing.c:89-91).
    """
    return append_bcc(bytes([0x33, cmd & 0xFF, 0x01, 0xFF]))


def build_splicing_sub_device_mac(untraced_0: int, mac: str, cmd: int = 0x40) -> bytes:
    """0x33 40 05 [untraced_0, MAC byte-reversed]: send a sub-device's SPP MAC to the main device
    (ControllerChangeSplicing.e:111-116). Meaning of untraced_0 untraced: every app caller sends 0
    (base-classes4/com/govee/splicing/splicingUi/DeviceSplicingActivity.java:543,727).
    """
    return append_bcc(
        bytes([0x33, cmd & 0xFF, 0x05, untraced_0 & 0xFF]) + bytes(int(x, 16) for x in mac.split(":"))[::-1]
    )


def read_status_with_ic(types: Sequence[int] = (0x40, 0x41, 0xA5)) -> bytes:
    """0xAC 03 [len, *types] status request carrying TLV 0x40 = IC count (Compose4BaseInfoSingleRead:166-179;
    VM4LightH667172$afterConnectedSingleReadDeviceInfo$1:160 for H6671/72; VM4Light.D5:720-725 gives (0x40,
    0xA5) for goodsType 351/352).
    """
    t = [x & 0xFF for x in types]
    return append_bcc(bytes([0xAC, 0x03, len(t), *t]))


def build_music_effect_a3_v2(
    music_code: int,
    colors: Sequence[tuple[int, int, int]],
    params: Sequence[int] = (),
    pad: int = 0,
    head: Sequence[int] = (),
) -> list[bytes]:
    """Music-effect value [head u8...][n][n x (R,G,B)][params u8...][pad zero bytes] framed as 0xA3 V2 comm
    [0x41, music_code] (base-classes10/com/govee/base2light/ble/music/RgbicMusicFenLi.java:84-99 and
    sibling AbsNewMusicEffect.toBytes;
    base-classes11/com/govee/base2light/mode/music/bean/MusicEffect.java:209-225;
    base-classes12/com/govee/base2light/view/h61d3/AbsNewMusicEffect4H61D3.java:60-76;
    base-classes10/com/govee/base2light/ble/controller/MultipleController4Music.java:20-24,39-45,56-57;
    base-classes10/com/govee/base2light/ble/comm/MultipleControllerCommV1.java:719-783,845-851). colors =
    [(r, g, b), ...].
    """
    value: bytes | bytearray = bytearray(h & 0xFF for h in head)
    cast(bytearray, value).append(len(colors) & 0xFF)
    for r, g, b in colors:
        value += bytes([r & 0xFF, g & 0xFF, b & 0xFF])
    value += bytes(p & 0xFF for p in params)
    value += bytes(pad)
    value = bytes(value)
    hdr = bytearray([0xA3, 0x00, 0x01, 0x00, 0x41, music_code & 0xFF])
    mids, tail = [], b""
    if len(value) <= 13:
        hdr += value
        count = 2
    else:
        hdr += value[:13]
        rest = value[13:]
        n = (len(rest) + 16) // 17
        for i in range(1, n + 1):
            chunk = rest[(i - 1) * 17 : i * 17]
            if i == n:
                tail = chunk
            else:
                mids.append(append_bcc(bytes([0xA3, i & 0xFF]) + chunk))
        count = n + 1
    hdr[3] = count & 0xFF
    return [append_bcc(bytes(hdr)), *mids, append_bcc(bytes([163, 255]) + tail)]


def build_on_off_memory(mode: int) -> bytes:
    """Power-on memory [0x02, mode] on fixed command 0x41 (OnOffMemoryController(type, true).q
    base-classes10/com/govee/base2light/ble/controller/OnOffMemoryController.java:18-22,75-78; callers
    split_rgbiclight/com/govee/dreamcolorlightv1/adjust/v2/UiV3.java:2726,2728).
    """
    return append_bcc(bytes([0x33, 0x41, 0x02, mode & 0xFF]))


def build_on_off_memory_legacy(on: bool) -> bytes:
    """0x33 41 [on] power-on memory boolean form, no 0x02 selector (ControllerOnOffMemory.Companion.h(-1, on),
    base-classes11/com/govee/base2light/kt/general_controller/ControllerOnOffMemory.java:79-81; caller
    H7031NewDetailVM.V4
    split_bulblightstringv1/com/govee/bulblightstringv2/newAdjust/h7031/H7031NewDetailVM.java:380).
    """
    return append_bcc(bytes([0x33, 0x41, 0x01 if on else 0x00]))


def read_on_off_memory() -> bytes:
    """AA 41 02 power-on memory read (OnOffMemoryController(0, false).p
    base-classes10/com/govee/base2light/ble/controller/OnOffMemoryController.java:18-22,64-66; caller
    split_rgbiclight/com/govee/dreamcolorlightv1/adjust/v2/BleOpV3.java:369).
    """
    return read_request(0x41, 0x02)


def read_on_off_memory_or_section_count() -> bytes:
    """0xAA 41 with no selector: kt newdetail power-on memory read (ControllerOnOffMemory.Companion.c,
    base-classes11/com/govee/base2light/kt/general_controller/ControllerOnOffMemory.java:49-58; callers
    split_rgbiclight/com/govee/rgbiclight/ControllerStrategyConfig.java:993,
    split_h61d3/com/govee/h61d3/adjustNew/VM4Light.java:576,
    split_bulblightstringv1/com/govee/bulblightstringv2/newAdjust/h7031/ControllerStrategyConfig.java:306).
    """
    return read_request(0x41)


def build_head_calibration(op: int, step: int | None = None) -> bytes:
    """0x33 42 [op, (step)] head calibration (CalibrationAcH60A6:258,306 via Controller4ExtBytes.f(66, ..);
    VM4LightH60A6.b5:805-817): 01 enter, 00 exit, 02 + step (1 = +1, 2 = -1 per H60A6 firmware), FF commit
    (app sends FF then 00).
    """
    return append_bcc(bytes([0x33, 0x42, op & 0xFF] + ([] if step is None else [step & 0xFF])))


def build_head_calibration_enter() -> bytes:
    """0x33 42 01 - enter head (zone-index) calibration (CalibrationAcH60A6.K1 :392 -> v2 :306
    Controller4ExtBytes.f(0x42, [01]) / VM4LightH60A6.H5 :674-682; CalibrationAcH1270 :297 ->
    VM4LightH1270.R5 :746-749; CalibrationAcH12D0 :401 -> VM4LightH12D0.K5 :655-658).
    """
    return append_bcc(bytes([0x33, 0x42, 0x01]))


def build_head_calibration_exit() -> bytes:
    """0x33 42 00 - leave head calibration without committing (CalibrationAcH60A6 j2 :178, k2 :186,
    onBackPressed :426 detail flow only -> v2([00]) :306; CalibrationAcH1270 :142,:150,:367;
    CalibrationAcH12D0 :178,:186,:435).
    """
    return append_bcc(bytes([0x33, 0x42, 0x00]))


def build_head_calibration_finish() -> list[bytes]:
    """[33 42 FF, 33 42 00] - Done: commit the current head index, then exit (CalibrationAcH60A6.f2 :107-111;
    VM4LightH60A6.b5 :805-811; VM4LightH1270.f5 :918-919; VM4LightH12D0.f5 :863-864; CalibrationAcH1270
    :333-334; CalibrationAcH12D0 :108-109).
    """
    return [append_bcc(bytes([0x33, 0x42, 0xFF])), append_bcc(bytes([0x33, 0x42, 0x00]))]


def build_head_calibration_step(right: bool) -> bytes:
    """0x33 42 02 <dir> - step the head index: right arrow ivYou [2,1] (+1), left arrow ivZuo [2,2] (-1)
    (CalibrationAcH60A6.i2 :161-174, m2 :207, n2 :212, s2 :258 / VM4LightH60A6.F5 :664-672;
    VM4LightH1270.N5 :702-705; VM4LightH12D0.I5 :635-638; H60A6_ble_dispatcher.c:526-540).
    """
    return append_bcc(bytes([0x33, 0x42, 0x02, 0x01 if right else 0x02]))


def build_refresh_ic() -> bytes:
    """0x33 42, no payload: refresh / write-check IC (ControllerRefreshIc:41-47; RefreshIcController.q:16-18;
    dreamcolorlightv1 RefreshIcController:11-17; Controller4ExtBytes.d:85-100 with [0x42]).
    """
    return append_bcc(bytes([0x33, 0x42]))


def build_splicing_cancel_bt() -> bytes:
    """33 42 0B exit splicing / cancel BT link (CancelBTController.q
    base-classes4/com/govee/splicing/controller/CancelBTController.java:16-25,41-44; caller
    base-classes4/com/govee/splicing/splicingUi/DeviceSplicingViewModel.java:436-442).
    """
    return append_bcc(bytes([0x33, 0x42, 0x0B]))


def build_spp_mac_write(mac: str) -> bytes:
    """33 42 0C <MAC octets reversed>: send the main device's SPP MAC to a sub device (SPPMacController(mac).q
    base-classes4/com/govee/splicing/controller/SPPMacController.java:32-36,68-71; BleUtils.d
    base-classes10/com/govee/base2kt/utils/BleUtils.java:554-568,592-608; caller
    base-classes4/com/govee/splicing/splicingUi/DeviceSplicingViewModel.java:451-461).
    """
    octets = [int(x, 16) & 0xFF for x in mac.split(":")][::-1]
    return append_bcc(bytes([0x33, 0x42, 0x0C, *octets]))


def read_spp_mac() -> bytes:
    """AA 42 06 splicing SPP MAC read (SPPMacController.p
    base-classes4/com/govee/splicing/controller/SPPMacController.java:26-29,38-40,45-52; KmpDiyConstant.f0
    base-classes4/com/govee/shared/protocol/oldgraffiti/KmpDiyConstant.java:78; caller
    base-classes4/com/govee/splicing/splicingUi/DeviceSplicingViewModel.java:396-398).
    """
    return read_request(0x42, 0x06)


def build_check_ic_amount(type_: int, ic: int = 0) -> bytes:
    """0x33 43 [type, ic u2be] IC amount / cut calibration (CheckICAmountController.q:31-34; rgbiclight
    Compose4LightCali:16-21; h6640 CheckIcController.Companion.a:29-35 sends [type] or [4, pos u2be],
    identical on the wire with ic=0 for type != 4).
    """
    return append_bcc(bytes([0x33, 0x43, type_ & 0xFF, (ic >> 8) & 0xFF, ic & 0xFF]))


def build_check_ic_h6640(check_type: int, ic_pos: int = 0) -> bytes:
    """0x33 43 [check_type] or [4][icPos u16be]: H6640 (242) / H6641 (247) cut calibration (h6640
    CheckIcController.Companion.a
    split_h61d3/com/govee/h61d3/adjustNew/setting/h6640/CheckIcController.java:29-35,46-48;
    CutCaliActivity.z2 split_h61d3/com/govee/h61d3/adjustNew/setting/h6640/CutCaliActivity.java:454-466).
    """
    if check_type != 4:
        return append_bcc(bytes([0x33, 0x43, check_type & 0xFF]))
    return append_bcc(bytes([0x33, 0x43, 0x04, (ic_pos >> 8) & 0xFF, ic_pos & 0xFF]))


def read_sub_device_version(cmd: int = 0x43) -> bytes:
    """0xAA 43, no selector (splicing ControllerReadSubDeviceVersion ctor:112-115, getCommandType:125)."""
    return read_request(cmd)


def build_calibration(on: bool) -> bytes:
    """0x33 44 [1|0] calibration on/off (h6047 CalibrationController.q:46-48; h6038
    CalibrationController.a:29-32 / d:47-50; H6048 CalibrationController4H6048.Companion.a:22-25 sends the
    empty payload = build_calibration(False)).
    """
    return append_bcc(bytes([0x33, 0x44, 0x01 if on else 0x00]))


def build_calibration_done_h6048() -> bytes:
    """0x33 44 with no payload: H6048 calibration done (CalibrationController4H6048.Companion.a
    split_h6047/com/govee/h6047/newdetail/control/CalibrationController4H6048.java:22-25,40-42).
    """
    return append_bcc(bytes([0x33, 0x44]))


def build_calibration_mode(into: bool) -> bytes:
    """0x33 44 02 [1 into | 0 exit] (base compose CalibrationController.Companion.a/b:30-38)."""
    return append_bcc(bytes([0x33, 0x44, 0x02, 0x01 if into else 0x00]))


def build_calibration_onoff(on: bool) -> bytes:
    """0x33 44 [1|0] calibration on/off for H6043 (196) and H6038 (288), not H6047 (h6047
    CalibrationController.q split_h6047/com/govee/h6047/ble/controller/CalibrationController.java:46-48
    gated by LightPosAc goodsType == 196; h6038 CalibrationController.Companion.a/d
    split_h6047/com/govee/h6047/newdetail/h6038/controller/CalibrationController.java:29-32,47-50).
    """
    return append_bcc(bytes([0x33, 0x44, 0x01 if on else 0x00]))


def build_cube_calibration(start: int, shape_type: int) -> bytes:
    """0x33 44 [start, shapeType] (cubelight CalibrationController.q:25-27)."""
    return append_bcc(bytes([0x33, 0x44, start & 0xFF, shape_type & 0xFF]))


def build_h6065_calibration(indices: Sequence[int], head: int = 15) -> bytes:
    """0x33 44 [head, idx...] (h6067 CalibrationControllerH6065.q:24-37; callers pass head 15)."""
    return append_bcc(bytes([0x33, 0x44, head & 0xFF, *[i & 0xFF for i in indices]]))


def build_h6067_calibration(index: int) -> bytes:
    """0x33 44 [index, 0] (h6067 CalibrationController.q:22-24; field g never assigned)."""
    return append_bcc(bytes([0x33, 0x44, index & 0xFF, 0x00]))


def read_splicing_status(cmd: int = 0x44) -> bytes:
    """0xAA 44, no selector (splicing SubDeviceReadSplicingStatusController ctor:77-79, getCommandType:90)."""
    return read_request(cmd)


def build_cube_install(installing: bool) -> bytes:
    """0x33 45 [0x00 entering install view | 0xFF leaving] - cube-light install-mode toggle
    (InstallController.q, split_h6061/com/govee/cubelight/ble/InstallController.java:17-23;
    split_h6061/com/govee/h6067/ble/InstallController.java:17-23).
    """
    return append_bcc(bytes([0x33, 0x45, 0x00 if installing else 0xFF]))


def build_install_mode(on: bool) -> bytes:
    """0x33 45 [00 if on else FF] cube / shape install mode, add-device flows (InstallController.q,
    split_h6061/com/govee/cubelight/ble/InstallController.java:9-23;
    split_h6061/com/govee/h6067/ble/InstallController.java:9-23).
    """
    return append_bcc(bytes([0x33, 0x45, 0x00 if on else 0xFF]))


def build_montage_brightness(level: int) -> bytes:
    """0x33 45 [level] - montage brightness write, no decomp caller (ControllerMontageBrightness
    ctor(int)/getCommandType,
    base-classes11/com/govee/base2light/kt/general_controller/ControllerMontageBrightness.java:50-53,61-63,73-75).
    """
    return append_bcc(bytes([0x33, 0x45, level & 0xFF]))


def read_montage_brightness() -> bytes:
    """0xAA 45 - montage brightness read, reply validBytes[0] = level u8, no decomp caller
    (ControllerMontageBrightness.Companion.b/c,
    base-classes11/com/govee/base2light/kt/general_controller/ControllerMontageBrightness.java:30-48).
    """
    return read_request(0x45)


def build_check_ic_step(step: int, cmd: int = 0x46) -> bytes:
    """33 46|5A [step]: strip-length calibration start 0 / cancel 3 / confirm 4; cmd 0x5A for the
    LightStripLenCaliConfig.c goodsTypes
    (base-classes10/com/govee/base2light/caliStripLen/CaliLastIcController.java:40-52,61-76,103-106;
    base-classes10/com/govee/base2light/caliStripLen/LightStripLenCaliConfig.java:37-45,47-59,268-271).
    CAUTION, firmware-sourced: with cmd 0x46 this frame factory-resets H60A1/H60A6 (their dispatcher runs
    the gateway-config and Matter reset, then reboots, on any 33 46 frame whatever the payload;
    H60A6_ble_dispatcher.c:454-468, H60A1_ble_dispatcher.c:389-400). Never send it to them.
    """
    return append_bcc(bytes([0x33, cmd & 0xFF, step & 0xFF]))


def build_reset_device_net(version: int = 1) -> bytes:
    """33 46 with no payload, reset device network, DESTRUCTIVE; version 2 gives 33 4E but every 7.7.02 caller
    passes version 1
    (base-classes11/com/govee/base2light/kt/general_controller/ControllerResetDeviceNet.java:35-46,48-63;
    callers
    base-classes11/com/govee/base2light/pact/newdetail/setting/common/ResetDeviceNetOpView.java:187,
    split_rgbiclight/com/govee/rgbiclight/SettingAc.java:397).
    """
    return append_bcc(bytes([0x33, 0x4E if version == 2 else 0x46]))


def build_splicing_change_package(flag: bool, cmd: int = 0x46) -> bytes:
    """0x33 46 [1|0] (splicing ControllerChangeSplicing.b:84-86; default cmd KmpDiyConstant.j0 :34-42)."""
    return append_bcc(bytes([0x33, cmd & 0xFF, 0x01 if flag else 0x00]))


def build_strip_len_cali_crop(ics: Sequence[int], extend: bool, cmd: int = 0x46) -> bytes:
    """0x33 46|5A [6 if extend else 5, n, ic u2be x n] (CaliLastIcController.d:85-95;
    LightStripLenCaliConfig.d:62-84 / e:87-90).
    """
    body = bytes([6 if extend else 5, len(ics) & 0xFF]) + b"".join(
        bytes([(v >> 8) & 0xFF, v & 0xFF]) for v in ics
    )
    return append_bcc(bytes([0x33, cmd & 0xFF]) + body[:17])


def build_part_scenes_47(seg_id: int, seg_indexes: Sequence[int] = ()) -> bytes:
    """0x33 05 47 [segId u16 LE, flags[5]] part scenes (SubModePartScenes.getWriteBytes,
    base-classes11/com/govee/base2light/pact/newdetail/content/scene/SubModePartScenes.java:34-42,53-55;
    flags byte[5] from PartScenesViewModel
    base-classes11/com/govee/base2light/pact/newdetail/content/scene/partScenes/viewModel/PartScenesViewModel.java:455-470).
    """
    flags = [0] * 5
    for i in seg_indexes:
        if 0 <= i < 5:
            flags[i] = 1
    return append_bcc(bytes([51, 5, 71, seg_id & 255, seg_id >> 8 & 255, *flags]))


def build_part_scenes_info(ext: bytes) -> bytes:
    """0x33 48 [extBytes...] part-scenes info write (ControllerPartScenesInfo.Companion.d,
    base-classes11/com/govee/base2light/kt/general_controller/ControllerPartScenesInfo.java:49-53,69-71; kt
    AbsController.q truncates to 17, base-classes11/com/govee/base2light/kt/ble/AbsController.java:47-60).
    No decompiled caller.
    """
    return append_bcc(bytes([0x33, 0x48]) + bytes(ext[:17]))


def read_part_scenes_info() -> bytes:
    """0xAA 48 - part-scenes segment/IC info read, raw 17 validBytes go to the callback unparsed, no decomp
    caller (ControllerPartScenesInfo.Companion.b/c,
    base-classes11/com/govee/base2light/kt/general_controller/ControllerPartScenesInfo.java:29-47,69-71).
    """
    return read_request(0x48)


def read_wifi_hard_version_v2() -> bytes:
    """0xAA 49 03 - Wi-Fi hard version read V2, reply validBytes[1..10] NUL-terminated ASCII
    (WifiHardVersionControllerV2,
    base-classes10/com/govee/base2light/ble/controller/WifiHardVersionControllerV2.java:17-38).
    """
    return read_request(0x49, 0x03)


def read_wifi_info_v2(selector: int) -> bytes:
    """0xAA 49 [sel] Wi-Fi info V2 read: 1 MAC, 2 soft, 3 hard (WifiMacControllerV2 /
    WifiSoftVersionControllerV2 / WifiHardVersionControllerV2.p,
    base-classes10/com/govee/base2light/ble/controller/WifiMacControllerV2.java:17-25; constructed by
    base-classes10/com/govee/base2light/connect/factory/ReadControllerFactory.java:40).
    """
    return read_request(0x49, selector)


def read_wifi_mac_v2() -> bytes:
    """0xAA 49 01 - Wi-Fi MAC read V2, reply validBytes[1..6] high-first
    (WifiMacControllerV2.p/parseValidBytes,
    base-classes10/com/govee/base2light/ble/controller/WifiMacControllerV2.java:17-46).
    """
    return read_request(0x49, 0x01)


def read_wifi_soft_version_v2() -> bytes:
    """0xAA 49 02 - Wi-Fi soft version read V2, reply validBytes[1..10] NUL-terminated ASCII
    (WifiSoftVersionControllerV2,
    base-classes10/com/govee/base2light/ble/controller/WifiSoftVersionControllerV2.java:17-38).
    """
    return read_request(0x49, 0x02)


def build_check_network() -> bytes:
    """0x33 4A [FF] dgw gateway network check (Controller4CheckNetwork.Companion.a/getCommandType,
    base-classes11/com/govee/base2light/form4Dbgw/Controller4CheckNetwork.java:25-44).
    """
    return append_bcc(bytes([0x33, 0x4A, 0xFF]))


def build_h6069_set_device_recognition(locations: list[int]) -> bytes:
    """H6069 cube light: send recognised block-location order [33 50 count loc...], ext truncated to 17 bytes
    (split_h6061/com/govee/cubelight/ble/SetDeviceRecognitionController.java:9-15;
    split_h6061/com/govee/cubelight/add/h6069/RecognitionSolveH6069Ac.java:495-506).
    """
    ext = bytes([len(locations) & 0xFF] + [loc & 0xFF for loc in locations])[:17]
    return append_bcc(bytes([0x33, 0x50]) + ext)


def build_movie_feast_set_sub_device(value: bytes) -> list[bytes]:
    """Movie-feast set sub-devices: 0xA3 multi-packet, comm byte 0x50, opaque caller-built value
    (base-classes10/com/govee/base2light/ble/controller/MultiSetSubDeviceController4MovieFeast.java:17-24,34-36;
    MultipleControllerCommV1.java:833-843).
    """
    return build_multi_a3_v1(0x50, value)


def build_cube_calibration_all_h6066(check_ics: list[int]) -> list[bytes]:
    """H6066 full calibration, 0xA3 V1 comm 0x52, value [0x04][n][check_ic_i + 6*i ...]
    (split_h6061/com/govee/cubelight/ble/CalibrationAllController.java:13-31,41-43,51-53;
    base-classes10/com/govee/base2light/ble/comm/MultipleControllerCommV1.java:715-783,833-843).
    """
    value = bytes([0x04, len(check_ics) & 0xFF] + [(ic + 6 * i) & 0xFF for i, ic in enumerate(check_ics)])
    start = bytearray([0xA3, 0x00, 0x01, 0x00, 0x52])
    mids, last = [], b""
    if len(value) <= 14:
        start += value
        total = 2
    else:
        start += value[:14]
        rest = value[14:]
        k = (len(rest) + 16) // 17
        for i in range(1, k + 1):
            chunk = rest[(i - 1) * 17 : i * 17]
            if i == k:
                last = chunk
            else:
                mids.append(append_bcc(bytes([0xA3, i & 0xFF]) + chunk))
        total = k + 1
    start[3] = total & 0xFF
    return [append_bcc(bytes(start)), *mids, append_bcc(bytes([163, 255]) + last)]


def build_h6067_calibration_add_not(
    head: int, shape_ics: list[int], count: int, other_ics: list[int]
) -> list[bytes]:
    """h6067 non-editable-shape calibration, 0xA3 V1 comm 0x52, value
    [head][shape_ics][01][head][count][other_ics]
    (split_h6061/com/govee/h6067/ble/CalibrationAddNotController.java:17-49,58-60,66-68;
    base-classes10/com/govee/base2light/ble/comm/MultipleControllerCommV1.java:715-783,833-843).
    """
    value = bytes(
        [head & 0xFF]
        + [v & 0xFF for v in shape_ics]
        + [0x01, head & 0xFF, count & 0xFF]
        + [v & 0xFF for v in other_ics]
    )
    start = bytearray([0xA3, 0x00, 0x01, 0x00, 0x52])
    mids, last = [], b""
    if len(value) <= 14:
        start += value
        total = 2
    else:
        start += value[:14]
        rest = value[14:]
        k = (len(rest) + 16) // 17
        for i in range(1, k + 1):
            chunk = rest[(i - 1) * 17 : i * 17]
            if i == k:
                last = chunk
            else:
                mids.append(append_bcc(bytes([0xA3, i & 0xFF]) + chunk))
        total = k + 1
    start[3] = total & 0xFF
    return [append_bcc(bytes(start)), *mids, append_bcc(bytes([163, 255]) + last)]


def build_h6067_calibration_all(
    head: int, shape_index: int, shape_ics: list[int], accumulated_ics: list[int]
) -> list[bytes]:
    """h6067 full calibration, 0xA3 V1 comm 0x52, value
    [head][shape_ics][01][head][shape_index-1][accumulated_ics], that tail zero-filled when shape_index <=
    0 (split_h6061/com/govee/h6067/ble/CalibrationAllController.java:15-47,57-59,65-67;
    base-classes10/com/govee/base2light/ble/comm/MultipleControllerCommV1.java:715-783,833-843).
    """
    if shape_index > 0:
        tail = bytes([(shape_index - 1) & 0xFF] + [v & 0xFF for v in accumulated_ics])
    else:
        tail = bytes(1 + len(accumulated_ics))
    value = bytes([head & 0xFF] + [v & 0xFF for v in shape_ics] + [0x01, head & 0xFF]) + tail
    start = bytearray([0xA3, 0x00, 0x01, 0x00, 0x52])
    mids, last = [], b""
    if len(value) <= 14:
        start += value
        total = 2
    else:
        start += value[:14]
        rest = value[14:]
        k = (len(rest) + 16) // 17
        for i in range(1, k + 1):
            chunk = rest[(i - 1) * 17 : i * 17]
            if i == k:
                last = chunk
            else:
                mids.append(append_bcc(bytes([0xA3, i & 0xFF]) + chunk))
        total = k + 1
    start[3] = total & 0xFF
    return [append_bcc(bytes(start)), *mids, append_bcc(bytes([163, 255]) + last)]


def build_movie_feast_on_off(on: bool) -> bytes:
    """Movie-feast on/off, legacy form (movieFeastVersion != 1): [33 54 open]
    (base-classes10/com/govee/base2light/ble/controller/MovieOpenController.java:11-14,22-24,33-35;
    AbsSingleController.java:22-24).
    """
    return append_bcc(bytes([0x33, 0x54, 0x01 if on else 0x00]))


def read_movie_feast_on_off() -> bytes:
    """Read movie-feast on/off, legacy form: [AA 54]; reply byte 2 = open
    (base-classes10/com/govee/base2light/ble/controller/MovieOpenController.java:7-9,27-30;
    AbsSingleController.java:17-19,37-43).
    """
    return read_request(0x54)


def build_carousel_frames(voice_switch: int, effects: Sequence[bytes]) -> list[bytes]:
    """0xA3 V1 multi write, comm 0x60, value [voiceSwitch][count][decoded effect records]: START [A3 00 01 n
    60 value[0:14]], MIDDLE [A3 i 17B], END [A3 FF rest]
    (base-classes11/com/govee/base2light/pact/newdetail/content/carousel/CarouselViewModel.java:585-608;
    base-classes11/com/govee/base2light/pact/newdetail/content/carousel/CarouselEffectManager.java:1154-1173;
    base-classes11/com/govee/base2light/kt/general_controller/MultiController.java:36-39,54-61;
    base-classes10/com/govee/base2light/ble/comm/MultipleControllerCommV1.java:715-783).
    """
    value = bytes([voice_switch & 0xFF, len(effects) & 0xFF]) + b"".join(bytes(e) for e in effects)
    if len(value) <= 14:
        return [append_bcc(bytes([0xA3, 0x00, 0x01, 0x02, 0x60]) + value), append_bcc(bytes([0xA3, 0xFF]))]
    rest = value[14:]
    n = len(rest) // 17 + (1 if len(rest) % 17 else 0)
    frames = [append_bcc(bytes([0xA3, 0x00, 0x01, (n + 1) & 0xFF, 0x60]) + value[:14])]
    for i in range(1, n + 1):
        frames.append(append_bcc(bytes([0xA3, 0xFF if i == n else i & 0xFF]) + rest[(i - 1) * 17 : i * 17]))
    return frames


def build_feast_module_movie_on_off(on: bool) -> bytes:
    """33 60 01 [open 1/0] 01: movie-feast on/off, module 0x60 sub 0x01, movieFeastVersion == 1
    (base-classes10/com/govee/base2light/ble/controller/MovieOpenControllerV2.java:11-14,22-24,38-40;
    base-classes10/com/govee/base2light/ble/controller/AbsSingleFeastController.java:21-24;
    base-classes10/com/govee/base2light/ble/controller/AbsSingleModuleController.java:20-23;
    base-classes11/com/govee/base2light/feast/cmd/FeastCmdOp.java:85-89).
    """
    return append_bcc(bytes([0x33, 0x60, 0x01, 0x01 if on else 0x00, 0x01]))


def build_feast_module_switch(on: bool) -> bytes:
    """33 60 01 [on 1/0]: music-feast on/off, module 0x60 sub 0x01, goodsType != 87
    (base-classes10/com/govee/base2light/ble/controller/SwitchControllerV2.java:13-16,23-26,36-39;
    base-classes10/com/govee/base2light/ble/controller/AbsSingleModuleController.java:20-23;
    base-classes11/com/govee/base2light/feast/cmd/FeastCmdOp.java:91-94).
    """
    return append_bcc(bytes([0x33, 0x60, 0x01, 0x01 if on else 0x00]))


def read_feast_module_movie_on_off() -> bytes:
    """AA 60 01 01: read movie-feast on/off, sub 0x01 at byte 2, selector p() = [1]; reply frame[3] = open
    (base-classes10/com/govee/base2light/ble/controller/MovieOpenControllerV2.java:7-9,26-35;
    base-classes10/com/govee/base2light/ble/controller/AbsSingleModuleController.java:15-18,30-36;
    base-classes11/com/govee/base2light/feast/cmd/FeastCmdOp.java:85-87,117-121).
    """
    return read_request(0x60, 0x01, 0x01)


def read_feast_module_switch() -> bytes:
    """AA 60 01: read music-feast on/off, sub 0x01 at byte 2, no selector; reply frame[3] != 0 means on
    (base-classes10/com/govee/base2light/ble/controller/SwitchControllerV2.java:9-11,28-34;
    base-classes10/com/govee/base2light/ble/controller/AbsSingleController.java:54-56;
    base-classes10/com/govee/base2light/ble/controller/AbsSingleModuleController.java:15-18,30-36;
    base-classes11/com/govee/base2light/feast/cmd/FeastCmdOp.java:94,117-121).
    """
    return read_request(0x60, 0x01)


def build_combination_effect_apply_mode(diy_id: int) -> bytes:
    """Default mode frame after the 0x62 transfer: [33 05 0A diyId u16le]
    (base-classes10/com/govee/base2light/ac/diyNew/views/combination/DiyAdvancedCombParse.java:72-78;
    kt/general_controller/ControllerMode.java:62-65).
    """
    return append_bcc(bytes([0x33, 0x05, 0x0A, diy_id & 0xFF, (diy_id >> 8) & 0xFF]))


def build_combination_effect_frames(value: bytes, mtu: int = 20) -> list[bytes]:
    """MULTI_COMBINATION_EFFECT 0x62 over 0xA4 MTU frames (variable length; last byte of each frame = XOR of
    the preceding bytes)
    (base-classes10/com/govee/base2light/ac/diyNew/views/combination/DiyAdvancedCombParse.java:70-84;
    base-classes11/com/govee/base2light/kt/ble/Compose4DefWrite4Multi.java:233-247;
    base-classes10/com/govee/base2light/ble/comm/MultipleControllerCommV1.java:366-493).
    """

    def x(b: bytes | bytearray) -> int:
        r = 0
        for v in b:
            r ^= v
        return r

    mtu = max(20, mtu)
    first, mid = mtu - 8, mtu - 4
    if len(value) <= first:
        s = bytes([0xA4, 0x00, 0x00, 0x01, 0x02, 0x00, 0x62]) + value
        t = bytes([0xA4, 0xFF, 0xFF])
        return [s + bytes([x(s)]), t + bytes([x(t)])]
    rest = len(value) - first
    r = rest % mid
    total = rest // mid + 1 + (1 if r else 0)
    s = bytes([0xA4, 0x00, 0x00, 0x01, total & 0xFF, (total >> 8) & 0xFF, 0x62]) + value[:first]
    frames = [s + bytes([x(s)])]
    for i in range(1, total):
        off = first + (i - 1) * mid
        if i == total - 1:
            body = bytes([0xA4, 0xFF, 0xFF]) + value[off : off + (mid if r == 0 else r)]
        else:
            body = bytes([0xA4, i & 0xFF, (i >> 8) & 0xFF]) + value[off : off + mid]
        frames.append(body + bytes([x(body)]))
    return frames


def build_diy_in_scenes_multi_a3(
    protocol: int, effect_bytes: bytes, mode_frame: bytes | None = None
) -> list[bytes]:
    """0xA3 V1 multi write with comm = DIY protocol code (protocol not 91/92), then the optional mode frame;
    the 0x62 (98) instance is reachable only through the unregistered DiyAdvancedCombParse
    (base-classes10/com/govee/base2light/ble/controller/DiyControllerNoEventV7.java:18-34;
    base-classes10/com/govee/base2light/ble/controller/AbsMultipleControllerV14DiyInScenes.java:9-33;
    base-classes10/com/govee/base2light/ble/comm/MultipleControllerCommV1.java:715-783,833-842; caller
    base-classes11/com/govee/base2light/pact/newdetail/content/diy/DiyApplyVM.java:1310).
    """
    if protocol in (91, 92):
        raise ValueError("protocol 91/92 goes through MultipleDiyV3Controller, not this framing")
    effect_bytes = bytes(effect_bytes)
    head = bytearray([0xA3, 0x00, 0x01, 0x00, protocol & 0xFF])
    mids, tail = [], b""
    if len(effect_bytes) <= 14:
        head += effect_bytes
        count = 2
    else:
        head += effect_bytes[:14]
        rest = effect_bytes[14:]
        n = (len(rest) + 16) // 17
        for i in range(1, n + 1):
            chunk = rest[(i - 1) * 17 : i * 17]
            if i == n:
                tail = chunk
            else:
                mids.append(append_bcc(bytes([0xA3, i & 0xFF]) + chunk))
        count = n + 1
    head[3] = count & 0xFF
    frames = [append_bcc(bytes(head)), *mids, append_bcc(bytes([163, 255]) + tail)]
    return frames + ([bytes(mode_frame)] if mode_frame is not None else [])


def build_daysync_node(
    node_id: int,
    time_code: int,
    hour: int,
    minute: int,
    brightness: int,
    transition_mode: int,
    action: Sequence[int],
) -> bytes:
    """One daysync node record (payload piece, not a frame): [param_len
    u4le][node_id][time_code][hour][minute][brightness][tm+1][tm+1][action...]. hour and minute are the
    clock time for time_code 1 and the offset's absolute hours and minutes for time_codes 2-7 (time_code
    from the node's time type and offset sign, DaySyncUtils.m :1024-1046); a weather node (time_code 8)
    carries its weather type u16le in those two bytes. DaySyncUtils.y
    (base-classes11/com/govee/base2light/pact/newdetail/content/daySync/DaySyncUtils.java:1426-1451), z
    (:1453-1480), I (:299-324).
    """
    body = bytes(
        [
            node_id & 0xFF,
            time_code & 0xFF,
            hour & 0xFF,
            minute & 0xFF,
            brightness & 0xFF,
            (transition_mode + 1) & 0xFF,
            (transition_mode + 1) & 0xFF,
        ]
    ) + bytes(action)
    return len(body).to_bytes(4, "little") + body


def build_daysync_scheme_incremental(
    scheme_short_id: int,
    repeat_frequency: int,
    add_nodes: Sequence[bytes],
    delete_ids: Sequence[int],
    mtu: int,
    device_kb: int,
    verify8: bytes | bytearray | Sequence[int] | Sequence[bytes],
) -> list[list[bytes]]:
    """0xA6 MTU transfer, cmd 0x63, incremental scheme update (op type 2): value = [id][repeat][2][n_add] +
    add/change node records + [n_delete][delete ids], the delete block being the LAST node element given to
    the packetiser. DaySyncUtils.i/h
    (base-classes11/com/govee/base2light/pact/newdetail/content/daySync/DaySyncUtils.java:878-924), x
    (:1409-1424), R (:586-681), j (:926-949), k (:951-1018), H (:264-297); sent via v -> w (:1384-1407).
    verify8 = one 8-byte value, or one per big package (H re-picks bytes 6-7 per package, :282-283).
    Returns big packages (lists of frames).
    """

    def _x(b: bytes | bytearray) -> int:
        x = 0
        for v in b:
            x ^= v
        return x

    nodes = [bytes(n) for n in add_nodes]
    del_block = bytes([len(delete_ids) & 0xFF] + [d & 0xFF for d in delete_ids])
    value = (
        bytes([scheme_short_id & 0xFF, repeat_frequency & 0xFF, 0x02, len(nodes) & 0xFF])
        + b"".join(nodes)
        + del_block
    )
    nodes = [*nodes, del_block]

    def unsplit_package(data: bytes) -> list[bytes | bytearray]:
        s = bytearray(len(data) + 18)
        s[0] = 0xA6
        s[3] = 0x01
        s[16] = 0x63
        s[17 : 17 + len(data)] = data
        e = bytes([0xA6, 0xFF, 0xFF])
        return [s, e + bytes([_x(e)])]

    def split_package(data: bytes, cap0: int, capn: int, maxpk: int) -> list[bytes | bytearray]:
        if len(data) <= cap0:
            return unsplit_package(data)
        s = bytearray(mtu)
        s[0] = 0xA6
        s[3] = 0x01
        s[16] = 0x63
        s[17 : 17 + cap0] = data[:cap0]
        out: list[bytes | bytearray] = [s]
        pos, i = cap0, 1
        while i < maxpk:
            if i == maxpk - 1 or pos + capn >= len(data):
                e = bytes([0xA6, 0xFF, 0xFF]) + bytes(data[pos:])
                out.append(e + bytes([_x(e)]))
                break
            m = bytes([0xA6, i & 0xFF, (i >> 8) & 0xFF]) + bytes(data[pos : pos + capn])
            out.append(m + bytes([_x(m)]))
            pos += capn
            i += 1
        return out

    maxpk = (device_kb * 1024) // mtu
    cap0 = mtu - 18
    capn = mtu - 4
    big = []
    if cap0 >= len(value):
        big.append(unsplit_package(value))
    else:
        cap_big = cap0 + (maxpk - 1) * capn
        acc, used = bytearray(value[:4]), 4
        for idx, node in enumerate(nodes):
            if used + len(node) > cap_big:
                if idx == 0:
                    return []
                big.append(split_package(bytes(acc), cap0, capn, maxpk))
                acc, used = bytearray(), 0
            if used == 0 and len(node) > cap_big:
                return []
            acc += node
            used += len(node)
            if idx == len(nodes) - 1:
                big.append(split_package(bytes(acc), cap0, capn, maxpk))
    if isinstance(verify8, (bytes, bytearray)) or all(isinstance(v, int) for v in verify8):
        verify = [bytes(verify8)[:8]] * len(big)  # type: ignore[arg-type]  # guard above restricts verify8 here to a flat byte sequence
    else:
        verify = [bytes(v)[:8] for v in verify8]
    for n, pk in enumerate(big):
        s = cast(bytearray, pk[0])
        s[4] = len(big)
        s[5] = n + 1
        s[6:14] = verify[n]
        s[14] = len(pk) & 0xFF
        s[15] = (len(pk) >> 8) & 0xFF
        s[-1] = _x(s[:-1])
    return [[bytes(f) for f in pk] for pk in big]


def build_daysync_space_check(
    scheme_id: int, deleted: Sequence[tuple[int, int]], added: Sequence[tuple[int, int]]
) -> list[bytes]:
    """0xA3 multi commByte 0x64 [scheme][n_del][(node, len u32le)*][n_add][(node, len u32le)*]
    (DaySyncUtils.C/F/y/J,
    base-classes11/com/govee/base2light/pact/newdetail/content/daySync/DaySyncUtils.java:154-172,188-209,1426-1451;
    MultiControllerOnResultParse.b
    base-classes11/com/govee/base2light/kt/general_controller/MultiControllerOnResultParse.java:50-55).
    """
    v = [scheme_id & 0xFF, len(deleted) & 0xFF]
    for nid, ln in deleted:
        v += [nid & 255, *list((ln & 4294967295).to_bytes(4, "little"))]
    v += [len(added) & 0xFF]
    for nid, ln in added:
        v += [nid & 255, *list((ln & 4294967295).to_bytes(4, "little"))]
    return build_multi_a3_v1(0x64, bytes(v))


def build_multi_a3_v1(comm: int, value: bytes) -> list[bytes]:
    """Generic 0xA3 multi-packet write with one comm byte: START [A3 00 01 count comm value[0:14]], MIDDLE i
    [A3 i 17B], data-bearing END [A3 FF <=17B]
    (base-classes10/com/govee/base2light/ble/comm/MultipleControllerCommV1.java:715-783
    makeSendBytesV1/makeSendBytesV2).
    """
    head = bytearray([0xA3, 0x00, 0x01, 0x00, comm & 0xFF])
    mids, tail = [], b""
    if len(value) <= 14:
        head += value
        count = 2
    else:
        head += value[:14]
        rest = value[14:]
        n = (len(rest) + 16) // 17
        for i in range(1, n + 1):
            chunk = rest[(i - 1) * 17 : i * 17]
            if i == n:
                tail = chunk
            else:
                mids.append(append_bcc(bytes([0xA3, i & 0xFF]) + chunk))
        count = n + 1
    head[3] = count & 0xFF
    return [append_bcc(bytes(head)), *mids, append_bcc(bytes([163, 255]) + tail)]


def build_daysync_weather_group(scheme_id: int, weather_nodes: Sequence[tuple[int, int, int]]) -> list[bytes]:
    """0xA3 multi commByte 0x65 [scheme][count][(time_code, h, m)*], count omitted when empty
    (DaySyncUtils.E/o/z,
    base-classes11/com/govee/base2light/pact/newdetail/content/daySync/DaySyncUtils.java:174-186,1112-1135,1453-1473;
    MultiController.a
    base-classes11/com/govee/base2light/kt/general_controller/MultiController.java:54-61).
    """
    v = [scheme_id & 0xFF]
    if weather_nodes:
        v += [len(weather_nodes) & 0xFF]
        for t, h, m in weather_nodes:
            v += [t & 0xFF, h & 0xFF, m & 0xFF]
    return build_multi_a3_v1(0x65, bytes(v))


def build_h5310_close_warn_via_gw(sno: int, on: bool) -> bytes:
    """0x33 70: H5310 close-warn wrapped for the gateway sub-device ([sno] + inner 33 16 01 e f frame[1:17]).
    base-classes12/com/govee/base2newth/gwsub/Controller4SubPtRealOp.java:34-45
    (Controller4SubPtRealOp.getCommandType/i);
    base-classes12/com/govee/base2newth/gwsub/Controller4H5310CloseWarn.java:21-35,55-64
    (ctor/getCommandType/i); base-classes12/com/govee/base2newth/gwsub/Dialog4H5310CloseWarn.java:362-386
    (Dialog4H5310CloseWarn.E).
    """
    if sno < 0:
        raise ValueError("app only sends when gateway sno >= 0 (Dialog4H5310CloseWarn.A)")
    e, f = (1, 0) if on else (2, 1)
    return append_bcc(bytes([0x33, 0x70, sno & 0xFF, 0x16, 0x01, e, f]))


def build_h5901_irrigation(sno: int, minutes: int) -> bytes:
    """0x33 70: H5901 eco irrigation action [sno, 0x01, 0x01, minutes u16le].
    base-classes13/com/govee/eco/executeAction/parser/IrrigationActionParser.java:54-57
    (IrrigationActionParser.z); base-classes10/com/govee/base2kt/utils/BleUtils.java:132-142
    (BleUtils.Companion.D with its big-endian flag false: low byte first).
    """
    return append_bcc(bytes([0x33, 0x70, sno & 0xFF, 0x01, 0x01, minutes & 0xFF, (minutes >> 8) & 0xFF]))


def build_h5901_switch_action(sno: int) -> bytes:
    """0x33 70: H5901 eco one-click switch action [sno, 0x01, 0, 0, 0] (on/off argument is ignored by the
    app). base-classes13/com/govee/eco/executeAction/parser/SwitchActionParserH5901.java:49-51,65-68
    (SwitchActionParserH5901.C/v).
    """
    return append_bcc(bytes([0x33, 0x70, sno & 0xFF, 0x01, 0x00, 0x00, 0x00]))


def build_h5901_sync_triggers(sno: int, t177: bool, t178: bool, t179: bool, t180: bool) -> bytes:
    """0x33 70: H5901 trigger sync [max(sno,0), 0x19, t177, t178, t179, t180].
    base-classes12/com/govee/base2light/synctriggers/h5901/ControllerSyncTriggers4H5901.java:30-35,46-53
    (Companion.a/getCommandType/q).
    """
    return append_bcc(
        bytes(
            [
                0x33,
                0x70,
                max(sno, 0) & 0xFF,
                0x19,
                int(bool(t177)),
                int(bool(t178)),
                int(bool(t179)),
                int(bool(t180)),
            ]
        )
    )


def read_music_lib_player_state() -> bytes:
    """AA 71, no selector: music-lib player-state read (no decompiled caller).
    base-classes11/com/govee/base2light/kt/general_controller/online/ControllerOnlineMusicPlayerState.java:25-42.
    """
    return read_request(0x71)


def build_music_lib_volume(volume: int) -> bytes:
    """33 79 [volume]: music-lib volume write (no decompiled caller).
    base-classes11/com/govee/base2light/kt/general_controller/online/ControllerOnlineVolume.java:53-59,62-65,83-90
    (Companion.b).
    """
    return append_bcc(bytes([0x33, 0x79, volume & 0xFF]))


def read_music_lib_volume() -> bytes:
    """AA 79, no selector: music-lib volume read (no decompiled caller).
    base-classes11/com/govee/base2light/kt/general_controller/online/ControllerOnlineVolume.java:45-51,67-75,83-90
    (Companion.a).
    """
    return read_request(0x79)


def read_diy_create_gif(diy_code: int, effect_op: int = 0) -> bytes:
    """0xAC 7B [0A, diy_code u16le, 01, effect_op]: create the DIY GIF and fetch it as an 0xAC multi-reply,
    NOTIFY_DEVICE_CREATE_GIF (DiyEdit4DeviceCreateGifActivity.g2
    base-classes10/com/govee/base2light/ac/diyNew/details/DiyEdit4DeviceCreateGifActivity.java:1948-1972;
    MaterialEditActivity.E2
    base-classes12/com/govee/base2light/widget/diy/view/material/MaterialEditActivity.java:308-327).
    """
    return append_bcc(
        bytes([0xAC, 0x7B, 0x0A, diy_code & 0xFF, (diy_code >> 8) & 0xFF, 0x01, effect_op & 0xFF])
    )


def build_carousel_apply_mode(carousel_id: int, voice_id: int = 0, voice_type: int = 0, op: int = 0) -> bytes:
    """0x33 05 82 [id u32 LE, voiceId u32 LE, voiceType, op] carousel mode (CarouselViewModel.e0,
    base-classes11/com/govee/base2light/pact/newdetail/content/carousel/CarouselViewModel.java:618-634; op
    1 via applyController in X :461-478 after deleteCarousel g0 :644-652; op 0 in d0 :585-616 and op 0/2 in
    a0 :499 / f0 :636-641 on the IoT path).
    """
    return append_bcc(
        bytes([0x33, 0x05, 0x82])
        + (carousel_id & 0xFFFFFFFF).to_bytes(4, "little")
        + (voice_id & 0xFFFFFFFF).to_bytes(4, "little")
        + bytes([voice_type & 0xFF, op & 0xFF])
    )


def build_display_remind_effect(
    category: int,
    slot: int,
    on: bool,
    voice_type: int,
    voice_code: int,
    effect_type: int,
    effect_code: int,
) -> bytes:
    """0x33 83 op 1 display remind per-slot voice/effect, codes u32 LE
    (EditDisplayRemindController.Companion.a,
    base-classes11/com/govee/base2light/pact/newdetail/content/display/EditDisplayRemindController.java:42-64;
    BleUtils.F base-classes10/com/govee/base2kt/utils/BleUtils.java:162-175).
    """
    return append_bcc(
        bytes([0x33, 0x83, 0x01, category & 0xFF, slot & 0xFF, 1 if on else 0, voice_type & 0xFF])
        + (voice_code & 0xFFFFFFFF).to_bytes(4, "little")
        + bytes([effect_type & 0xFF])
        + (effect_code & 0xFFFFFFFF).to_bytes(4, "little")
    )


def build_display_remind_global(
    close_all: bool,
    start_hour: int,
    start_minute: int,
    end_hour: int,
    end_minute: int,
    repeat: int,
    nba_on: bool,
    nfl_on: bool,
    env_on: bool,
) -> bytes:
    """0x33 83 op 0 display remind global settings (EditDisplayRemindController.Companion.b,
    base-classes11/com/govee/base2light/pact/newdetail/content/display/EditDisplayRemindController.java:66-91,97-99).
    """
    return append_bcc(
        bytes(
            [
                0x33,
                0x83,
                0x00,
                1 if close_all else 0,
                start_hour & 0xFF,
                start_minute & 0xFF,
                end_hour & 0xFF,
                end_minute & 0xFF,
                repeat & 0xFF,
                1 if nba_on else 0,
                1 if nfl_on else 0,
                1 if env_on else 0,
            ]
        )
    )


def build_mic_set_rgb(r: int, g: int, b: int) -> bytes:
    """0xA5 mic colour frame [A5 02 83 R G B sum8]: 7 bytes, unpadded, additive checksum (not a 20-byte XOR
    frame). AbsMicController.g
    (base-classes10/com/govee/base2light/ble/mic/controller/AbsMicController.java:31-52) +
    MicSetRgbController.getCommandType
    (base-classes10/com/govee/base2light/ble/mic/controller/MicSetRgbController.java:47-49).
    """
    body = bytes([0xA5, 0x02, 0x83, r & 0xFF, g & 0xFF, b & 0xFF])
    return body + bytes([sum(body) & 0xFF])


def read_display_remind() -> bytes:
    """0xAC 83 multi-reply display-remind read (SetDisplayRemindActivity.T1 makeSingleRead(-125),
    base-classes11/com/govee/base2light/pact/newdetail/content/display/activity/SetDisplayRemindActivity.java:827-831;
    proType 0xAC
    base-classes10/com/govee/base2light/ble/controller/AbsController4BleSingleSendMultiBack.java:158-165).
    """
    return append_bcc(bytes([0xAC, 0x83]))


def build_alarm_clock_apply_effect(
    effect_frames: Sequence[bytes], alarm_frame: bytes, prefix_frames: Sequence[bytes] = ()
) -> list[bytes]:
    """Alarm add/edit with effect_type 1 (scene) / 2 (DIY) on the paths where the 33 84 alarm frame takes the
    place of the 33 05 mode frame: BaseSceneViewMode.k3 list/MTU/V3 branches
    (base-classes11/com/govee/base2light/pact/newdetail/ content/scene/BaseSceneViewMode.java:915-984), k3
    -> r3 scene type 5 (:883-896), DiyApplyVM.r0
    (base-classes11/com/govee/base2light/pact/newdetail/content/diy/DiyApplyVM.java:1553-1557,1572);
    prefix_frames = optional switch-on frames from ApplyOffNeedOpenHelper.b/d
    (base-classes11/com/govee/base2light/pact/newdetail/ config/fuc/ApplyOffNeedOpenHelper.java:51-106).
    Not used on PartScenesViewModel.W (k3 :910-914) or DiyApplyVM.Q -> e0 (DiyApplyVM.java:913-934), which
    drop the alarm frame.
    """
    return [bytes(f) for f in prefix_frames] + [bytes(f) for f in effect_frames] + [bytes(alarm_frame)]


def build_alarm_clock_delete(alarm_id: int) -> bytes:
    """33 84 op 2: delete display alarm.
    base-classes11/com/govee/base2light/pact/newdetail/content/display/EditAlarmClockController.java:77-82
    (EditAlarmClockController.Companion.b); caller
    base-classes11/com/govee/base2light/pact/newdetail/content/display/activity/EditDisplayAlarmClockActivity.java:1229.
    """
    return append_bcc(bytes([0x33, 0x84, 0x02, alarm_id & 0xFF]))


def build_alarm_clock_edit(
    op: int,
    alarm_id: int,
    on: bool,
    later_remind: bool,
    effect_on: bool,
    voice_on: bool,
    hour: int,
    minute: int,
    repeat: int,
    brightness: int,
    effect_type: int,
    effect_code: int,
    voice_volume: int,
    voice_code: int,
    voice_type: int,
) -> bytes:
    """33 84 op 0 add / op 1 edit display alarm.
    base-classes11/com/govee/base2light/pact/newdetail/content/display/EditAlarmClockController.java:35-74,113-115
    (EditAlarmClockController.Companion.a); caller
    base-classes11/com/govee/base2light/pact/newdetail/content/display/activity/EditDisplayAlarmClockActivity.java:1426-1460
    (l2; op 0 from :562, op 1 from :578).
    """
    flags = (
        (0x80 if on else 0)
        | (0x40 if later_remind else 0)
        | (0x20 if effect_on else 0)
        | (0x10 if voice_on else 0)
    )
    return append_bcc(
        bytes(
            [
                0x33,
                0x84,
                op & 0xFF,
                alarm_id & 0xFF,
                flags,
                hour & 0xFF,
                minute & 0xFF,
                repeat & 0xFF,
                brightness & 0xFF,
                effect_type & 0xFF,
            ]
        )
        + (effect_code & 0xFFFF).to_bytes(2, "little")
        + bytes([voice_volume & 0xFF])
        + (voice_code & 0xFFFFFFFF).to_bytes(4, "little")
        + bytes([voice_type & 0xFF])
    )


def build_alarm_clock_edit_days(
    op: int,
    alarm_id: int,
    on: bool,
    later_remind: bool,
    effect_on: bool,
    voice_on: bool,
    hour: int,
    minute: int,
    days: Sequence[int],
    brightness: int,
    effect_type: int,
    effect_code: int,
    voice_volume: int,
    voice_code: int,
    voice_type: int,
) -> bytes:
    """0x33 0x84 op 0/1 alarm add/edit with the repeat byte built from days = 7 bools Mon..Sun. Repeat =
    NumUtil.getRepeat (base-classes12/com/govee/base2light/util/NumUtil.java:30-44) as called by
    EditDisplayAlarmClockActivity.h3
    (base-classes11/com/govee/base2light/pact/newdetail/content/display/activity/
    EditDisplayAlarmClockActivity.java:1271-1279); frame = EditAlarmClockController.Companion.a
    (base-classes11/com/govee/base2light/pact/newdetail/content/display/EditAlarmClockController.java:35-74).
    """
    days = [bool(d) for d in days]
    if len(days) != 7:
        raise ValueError("days must hold 7 entries, Monday first")
    repeat = 0x00 if all(days) else 0x80 | sum(1 << i for i, d in enumerate(days) if d)
    flags = (
        (0x80 if on else 0)
        | (0x40 if later_remind else 0)
        | (0x20 if effect_on else 0)
        | (0x10 if voice_on else 0)
    )
    return append_bcc(
        bytes(
            [
                0x33,
                0x84,
                op & 0xFF,
                alarm_id & 0xFF,
                flags,
                hour & 0xFF,
                minute & 0xFF,
                repeat,
                brightness & 0xFF,
                effect_type & 0xFF,
            ]
        )
        + (effect_code & 0xFFFF).to_bytes(2, "little")
        + bytes([voice_volume & 0xFF])
        + (voice_code & 0xFFFFFFFF).to_bytes(4, "little")
        + bytes([voice_type & 0xFF])
    )


def build_alarm_clock_switch(
    alarm_id: int, on: bool, later_remind: bool, effect_on: bool, voice_on: bool
) -> bytes:
    """33 84 op 3: toggle display alarm, flags as op 0/1.
    base-classes11/com/govee/base2light/pact/newdetail/content/display/EditAlarmClockController.java:85-102
    (EditAlarmClockController.Companion.c); caller
    base-classes11/com/govee/base2light/pact/newdetail/content/display/activity/DisplayAlarmClockListActivity.java:547.
    """
    flags = (
        (0x80 if on else 0)
        | (0x40 if later_remind else 0)
        | (0x20 if effect_on else 0)
        | (0x10 if voice_on else 0)
    )
    return append_bcc(bytes([0x33, 0x84, 0x03, alarm_id & 0xFF, flags]))


def read_alarm_clock_list() -> bytes:
    """AC 84 single request, multi-frame reply (all display alarms).
    base-classes11/com/govee/base2light/pact/newdetail/content/display/activity/DisplayAlarmClockListActivity.java:375-392
    (H1 -> ControllerSingleRead.makeSingleRead(0x84), sendWriteCmd :390); parser f2 :575-629.
    """
    return append_bcc(bytes([0xAC, 0x84]))


def build_alarm_clock_ring_action(remind_later: bool) -> bytes:
    """0x33 85 [0 close | 1 remind later] answer a ringing alarm (CloseAlarmClockController.Companion.a,
    base-classes11/com/govee/base2light/pact/newdetail/content/display/CloseAlarmClockController.java:26-41;
    DelegateDisplay.s0/t0
    base-classes11/com/govee/base2light/pact/newdetail/content/display/DelegateDisplay.java:479-486,596-605).
    """
    return append_bcc(bytes([0x33, 0x85, 0x01 if remind_later else 0x00]))


def build_diy_create_gif(diy_code: int, diy_id: int) -> bytes:
    """0x33 86 [01, diy_code u16 LE, diyId u32 LE] create DIY GIF (DiyEdit4DeviceCreateGifActivity.l2,
    base-classes10/com/govee/base2light/ac/diyNew/details/DiyEdit4DeviceCreateGifActivity.java:1974-1989;
    the u16 is the activity's DIY code, the value it stores as the DiyValue's diyCode, :210,294,399,1624).
    """
    return append_bcc(
        bytes([0x33, 0x86, 0x01])
        + (diy_code & 0xFFFF).to_bytes(2, "little")
        + (diy_id & 0xFFFFFFFF).to_bytes(4, "little")
    )


def build_diy_create_gif_v1(diy_code: int, diy_id: int) -> bytes:
    """0x33 86 [01, diy_code u16le, diy_id u32le]: ask the device to create the DIY GIF/icon,
    NOTIFY_DEVICE_CREATE_UPLOAD_GIF (DiyEdit4DeviceCreateGifActivity.l2
    base-classes10/com/govee/base2light/ac/diyNew/details/DiyEdit4DeviceCreateGifActivity.java:1974-1989;
    ControllerSingle.Companion.c
    base-classes11/com/govee/base2light/kt/general_controller/ControllerSingle.java:38-41).
    """
    return append_bcc(
        bytes(
            [
                0x33,
                0x86,
                0x01,
                diy_code & 0xFF,
                (diy_code >> 8) & 0xFF,
                *(diy_id & 0xFFFFFFFF).to_bytes(4, "little"),
            ]
        )
    )


def read_special_info_a1() -> bytes:
    """FACTORY-ONLY (no decompiled caller): 0xAC A1 single-send/multi-back read, reply matched on byte5 =
    0xA1, byte6 = sub 0x0B (ControllerReadSpecialInfoMultiBack.Companion.k
    base-classes11/com/govee/base2light/kt/general_controller/ControllerReadSpecialInfoMultiBack.java:100-109;
    AbsController4BleSingleSendMultiBack.f/getNextCommBytes/getProType
    base-classes10/com/govee/base2light/ble/controller/AbsController4BleSingleSendMultiBack.java:100-103,158-165).
    """
    return append_bcc(bytes([0xAC, 0xA1]))


def read_bulb_group_color(group: int) -> bytes:
    """AA A2 <group> (1-based) colour-only group read.
    base-classes11/com/govee/base2light/kt/general_controller/Controller4ColorInfoByGroup.java:44-49,122-131;
    split_rgbiclight/com/govee/rgbiclight/VM4Light.java:882-970 (N5).
    """
    return read_request(0xA2, group)


def read_color_group(group: int) -> bytes:
    """AA A5 <group> (1-based) brightness+colour group read.
    base-classes11/com/govee/base2light/kt/general_controller/Controller4ColorInfoByGroup.java:37-42,111-120;
    split_rgbiclight/com/govee/rgbiclight/VM4Light.java:942-949 (N5).
    """
    return read_request(0xA5, group)


def build_abs_music_blob(blob: bytes, music_id: int) -> list[bytes]:
    """0xA3 V1 multi-packet, comm 0x42 + decoded cloud music blob, then 33 05 16 [music_id u16le] FF
    (MusicComposeController).
    base-classes10/com/govee/base2light/ble/controller/MultiControllerNoEvent.java:16-22 (<init>:
    makeSendBytesV1(0xA3, 0x42, blob));
    base-classes10/com/govee/base2light/ble/controller/MusicComposeController.java:27-38 (<init>: multi
    first, then single); base-classes10/com/govee/base2light/ac/music/MusicApplyM.java:610-617
    (MusicApplyM.m); base-classes10/com/govee/base2light/ble/comm/MultipleControllerCommV1.java:715-783
    (makeSendBytesV1/V2).
    """
    value = bytes(blob)
    head = bytearray([0xA3, 0x00, 0x01, 0x00, 0x42])
    mids, tail = [], b""
    if len(value) <= 14:
        head += value
        count = 2
    else:
        head += value[:14]
        rest = value[14:]
        chunks = [rest[i : i + 17] for i in range(0, len(rest), 17)]
        mids = [append_bcc(bytes([0xA3, n & 0xFF]) + c) for n, c in enumerate(chunks[:-1], start=1)]
        tail = chunks[-1]
        count = len(chunks) + 1
    head[3] = count & 0xFF
    return [
        append_bcc(bytes(head)),
        *mids,
        append_bcc(bytes([0xA3, 0xFF]) + tail),
        append_bcc(bytes([0x33, 0x05, 0x16, music_id & 0xFF, (music_id >> 8) & 0xFF, 0xFF])),
    ]


def build_breath_train_multi_a3(data: bytes) -> list[bytes]:
    """0xA3 multi-packet upload with op 0x70 (breath training): header A3 00 01 N 70 + data[0:14], A3 i + 17
    bytes, A3 FF + rest.
    base-classes11/com/govee/base2light/kt/general_controller/MultiController.java:36-39,54-61
    (Companion.a/<init>);
    base-classes10/com/govee/base2light/ble/comm/MultipleControllerCommV1.java:715-782
    (makeSendBytesV1/makeSendBytesV2);
    base-classes11/com/govee/base2light/pact/newdetail/content/breath/BreathTrainRunningAc.java:245-255
    (BreathTrainRunningAc.J2).
    """
    data = bytes(data)
    head = bytearray(19)
    head[0:3] = b"\xa3\x00\x01"
    head[4] = 0x70
    term = bytearray(19)
    term[0] = 0xA3
    term[1] = 0xFF
    mids = []
    if len(data) <= 14:
        head[5 : 5 + len(data)] = data
        n = 2
    else:
        head[5:19] = data[:14]
        rest = data[14:]
        chunks = [rest[i : i + 17] for i in range(0, len(rest), 17)]
        for idx, ch in enumerate(chunks[:-1], start=1):
            mids.append(append_bcc(bytes([0xA3, idx & 0xFF]) + ch))
        term[2 : 2 + len(chunks[-1])] = chunks[-1]
        n = len(chunks) + 1
    head[3] = n & 0xFF
    return [append_bcc(bytes(head)), *mids, append_bcc(bytes(term))]


def build_color_strip_apply(
    colors: Sequence[int],
    brightness: Sequence[int] | None = None,
    color_instruction_version: int | None = None,
) -> list[bytes]:
    """Control-screen colour-strip apply (ColorModeControllerConfig.w -> m): build_color_strip_multi frames,
    then 33 05 [0x0C if colorInstructionVersion (Q0) == 10 else 0x15]
    (base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/ColorModeControllerConfig.java:937-951
    Companion.m; base-classes11/com/govee/base2light/pact/ble/OperateModeInfo.java:22).
    """
    mode = 0x0C if color_instruction_version == 10 else 0x15
    return [*build_color_strip_multi(colors, brightness), append_bcc(bytes([51, 5, mode]))]


def _java_hashmap_order(keys: Sequence[_T], hashcode: Callable[[_T], int]) -> list[_T]:
    """Key-set iteration order of a java.util.HashMap (OpenJDK 8 / Android API 24+) filled by put() of
    distinct keys in the order given: capacity 16, load factor 0.75, a bin reaching 9 nodes forces resize()
    while capacity < 64. Treeified bins (capacity >= 64) are not modelled.
    """

    def spread(key: _T) -> int:
        h = hashcode(key) & 0xFFFFFFFF
        return h ^ (h >> 16)

    table: list[list[_T]] | None
    table, cap, size, threshold = None, 0, 0, 0

    def resize(old: list[list[_T]] | None, old_cap: int) -> tuple[list[list[_T]], int, int]:
        new_cap = 16 if old_cap == 0 else old_cap * 2
        new: list[list[_T]] = [[] for _ in range(new_cap)]
        for b in old or []:
            for k in b:
                new[spread(k) & (new_cap - 1)].append(k)
        return new, new_cap, (new_cap * 3) // 4

    for k in keys:
        if table is None:
            table, cap, threshold = resize(None, 0)
        b = table[spread(k) & (cap - 1)]
        if k in b:
            continue
        b.append(k)
        if len(b) >= 9:
            if cap < 64:
                table, cap, threshold = resize(table, cap)
            else:
                raise NotImplementedError("treeified HashMap bin: iteration order not modelled")
        size += 1
        if size > threshold:
            table, cap, threshold = resize(table, cap)
    return [k for b in (table or []) for k in b]


def _group(values: Sequence[_K], labels: Sequence[_V]) -> tuple[list[_K], dict[_K, list[_V]]]:
    """{value: [label...]} in first-seen order, like the app's HashMap<value, ArrayList> fill loop."""
    members: dict[_K, list[_V]]
    order, members = [], {}
    for v, lab in zip(values, labels, strict=False):
        if v not in members:
            members[v] = []
            order.append(v)
        members[v].append(lab)
    return order, members


def build_color_strip_multi(
    colors: Sequence[int],
    brightness: Sequence[int] | None = None,
    pos_list: Sequence[int] | None = None,
) -> list[bytes]:
    """0xA3 V1 comm 0x40 per-segment colour strip: value [groupCount] + colour groups [0][n][R][G][B][idx x n]
    + brightness groups [1][n][bri][idx x n], each set in java.util.HashMap key order
    (base-classes10/com/govee/base2light/ble/controller/AbsMultipleControllerV14ColorStrip.java:23-93,197-205;
    MultipleColorStripControllerV1.java:7-18;
    base-classes11/com/govee/base2light/kt/general_controller/MultiColorStripControllerNoEvent.java:36-40,50-52).
    """
    colors = [c & 0xFFFFFFFF for c in colors]
    labels = [pos_list[i] if pos_list is not None and len(pos_list) > i else i for i in range(len(colors))]
    c_first, c_members = _group(colors, labels)
    c_keys = _java_hashmap_order(c_first, lambda k: k)
    b_members: dict[int, list[int]]
    b_keys, b_members = [], {}
    if brightness is not None:
        b_first, b_members = _group(list(brightness), list(range(len(brightness))))
        b_keys = _java_hashmap_order(b_first, lambda k: k)
    v = [len(c_keys) + len(b_keys)]
    for c in c_keys:
        idx = c_members[c]
        v += [0, len(idx), c >> 16 & 255, c >> 8 & 255, c & 255, *list(idx)]
    for bri in b_keys:
        idx = b_members[bri]
        v += [1, len(idx), bri, *list(idx)]
    return build_multi_a3_v1(0x40, bytes(x & 0xFF for x in v))


def build_color_tem_strip_apply(
    colors: Sequence[int],
    kelvin_of: dict[int, int] | None,
    brightness: Sequence[int] | None = None,
    color_instruction_version: int | None = None,
) -> list[bytes]:
    """Control-screen colour-temperature strip apply (ColorModeControllerConfig.w -> n):
    build_color_tem_strip_multi frames, then 33 05 [0x0C if colorInstructionVersion (Q0) == 10 else 0x15]
    (base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/ColorModeControllerConfig.java:954-966
    Companion.n, :1114-1124 w).
    """
    mode = 0x0C if color_instruction_version == 10 else 0x15
    return [*build_color_tem_strip_multi(colors, kelvin_of, brightness), append_bcc(bytes([51, 5, mode]))]


def build_color_tem_strip_multi(
    colors: Sequence[int],
    kelvin_of: dict[int, int] | None,
    brightness: Sequence[int] | None = None,
    pos_list: Sequence[int] | None = None,
) -> list[bytes]:
    """0xA3 V1 comm 0x40 colour+kelvin strip: [groupCount] + colour groups [0][n][R][G][B][idx] + brightness
    groups [1][n][bri][idx] + kelvin groups [2][n][K u16be][R][G][B of the colour][idx]; kelvin_of maps
    each colour the app classes as colour temperature (KelvinConfig.i) to KelvinConfig.c
    (base-classes10/com/govee/base2light/ble/controller/AbsMultipleControllerColorTemStrip.java:59-186,193-206;
    MultipleColorTemStripController.java:48-51;
    base-classes11/com/govee/base2light/kt/general_controller/MultiColorTemStripControllerNoEvent.java:41-46).
    """
    colors = [c & 0xFFFFFFFF for c in colors]
    kel = {k & 0xFFFFFFFF: v for k, v in dict(kelvin_of or {}).items()}
    labels = [pos_list[i] if pos_list is not None and len(pos_list) > i else i for i in range(len(colors))]
    rgb_vals = [(c, lab) for c, lab in zip(colors, labels, strict=False) if c not in kel]
    tem_vals = [(c, lab) for c, lab in zip(colors, labels, strict=False) if c in kel]
    c_first, c_members = _group([c for c, _ in rgb_vals], [lab for _, lab in rgb_vals])
    t_first, t_members = _group([c for c, _ in tem_vals], [lab for _, lab in tem_vals])
    c_keys = _java_hashmap_order(c_first, lambda k: k)
    t_keys = _java_hashmap_order(t_first, lambda k: k)
    b_members: dict[int, list[int]]
    b_keys, b_members = [], {}
    if brightness is not None:
        b_first, b_members = _group(list(brightness), list(range(len(brightness))))
        b_keys = _java_hashmap_order(b_first, lambda k: k)
    v = [len(c_keys) + len(b_keys) + len(t_keys)]
    for c in c_keys:
        idx = c_members[c]
        v += [0, len(idx), c >> 16 & 255, c >> 8 & 255, c & 255, *list(idx)]
    for bri in b_keys:
        idx = b_members[bri]
        v += [1, len(idx), bri, *list(idx)]
    for c in t_keys:
        idx = t_members[c]
        k = kel[c]
        v += [2, len(idx), k >> 8 & 255, k & 255, c >> 16 & 255, c >> 8 & 255, c & 255, *list(idx)]
    return build_multi_a3_v1(0x40, bytes(x & 0xFF for x in v))


def build_gradient_color_strip_apply(
    colors: Sequence[int],
    segments: int,
    kelvin_of: dict[int, int] | None = None,
    brightness: Sequence[int] | None = None,
) -> list[bytes]:
    """Control-screen gradient strip apply for `segments` (Info4BleIotDevice.J) segments: J colours are
    tripled, otherwise copied into a zeroed 3J array; a 3J brightness list keeps the first of each triple;
    then build_gradient_color_tem_strip_multi frames and 33 05 15
    (base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/GradientColorControllerConfig.java:230-265
    j, :430-455 x).
    """
    if len(colors) == segments:
        full = [c for c in colors for _ in range(3)]
    else:
        full = (list(colors) + [0] * (3 * segments))[: 3 * segments]
    if brightness is not None and len(brightness) == 3 * segments:
        brightness = list(brightness)[0::3]
    return [
        *build_gradient_color_tem_strip_multi(full, kelvin_of, brightness),
        append_bcc(bytes([51, 5, 21])),
    ]


def _java_int(v: int) -> int:
    v &= 0xFFFFFFFF
    return v - (1 << 32) if v & 0x80000000 else v


def _java_string_hash(s: str) -> int:
    h = 0
    for ch in s:
        h = (31 * h + ord(ch)) & 0xFFFFFFFF
    return h


def build_gradient_color_tem_strip_multi(
    colors: Sequence[int],
    kelvin_of: dict[int, int] | None = None,
    brightness: Sequence[int] | None = None,
    pos_list: Sequence[int] | None = None,
) -> list[bytes]:
    """0xA3 V1 comm 0x40 gradient strip: colours chunked by 3 per segment; [groupCount] + gradient groups
    [3][n][RGB x chunk][idx] (HashMap<String> order) + brightness groups [1][n][bri][idx] + kelvin groups
    [2][n][K u16be][Color.red/green/blue(K)][idx]; kelvin_of maps a colour to
    ColorUtils.h(colour,1000,10000)
    (base-classes10/com/govee/base2light/ble/controller/AbsMultipleControllerGradientColorTemStrip.java:40-193,200-213;
    MultipleGradientColorTemStripController.java:29-32;
    base-classes11/com/govee/base2light/kt/general_controller/MultiGradientColorTemStripControllerNoEvent.java:36-40).
    """
    colors = [c & 0xFFFFFFFF for c in colors]
    kel = {k & 0xFFFFFFFF: v for k, v in dict(kelvin_of or {}).items()}
    chunks = [colors[i : i + 3] for i in range(0, len(colors), 3)]
    labels = [pos_list[i] if pos_list is not None and len(pos_list) > i else i for i in range(len(chunks))]
    g_vals, t_vals = [], []
    for ch, lab in zip(chunks, labels, strict=False):
        same = len(ch) > 0 and all(c == ch[0] for c in ch)
        if same and ch[0] in kel:
            t_vals.append((kel[ch[0]], lab))
        else:
            g_vals.append((", ".join(str(_java_int(c)) for c in ch), lab))
    g_first, g_members = _group([k for k, _ in g_vals], [lab for _, lab in g_vals])
    t_first, t_members = _group([k for k, _ in t_vals], [lab for _, lab in t_vals])
    g_keys = _java_hashmap_order(g_first, _java_string_hash)
    t_keys = _java_hashmap_order(t_first, lambda k: k)
    b_members: dict[int, list[int]]
    b_keys, b_members = [], {}
    if brightness is not None:
        b_first, b_members = _group(list(brightness), list(range(len(brightness))))
        b_keys = _java_hashmap_order(b_first, lambda k: k)
    v = [len(g_keys) + len(b_keys) + len(t_keys)]
    for s in g_keys:
        idx = g_members[s]
        rgb = []
        for c in (int(x) & 0xFFFFFFFF for x in s.split(", ")):
            rgb += [(c >> 16) & 0xFF, (c >> 8) & 0xFF, c & 0xFF]
        v += [3, len(idx), *rgb, *list(idx)]
    for bri in b_keys:
        idx = b_members[bri]
        v += [1, len(idx), bri, *list(idx)]
    for k in t_keys:
        idx = t_members[k]
        v += [2, len(idx), k >> 8 & 255, k & 255, k >> 16 & 255, k >> 8 & 255, k & 255, *list(idx)]
    return build_multi_a3_v1(0x40, bytes(x & 0xFF for x in v))


def build_h60ax_new_music_multi(music_code: int, effect: bytes) -> list[bytes]:
    """0xA3 V2 multi-packet, comm [0x41, music_code] + effect bytes, then 33 05 13 05 64 (h60ax
    NewMusicMultipleController, legacy UiV1/CommOpV1 path).
    split_h60ax/com/govee/pact_h60a0/ble/controller/compose/NewMusicMultipleController.java:19-30
    (NewMusicMultipleController.<init>);
    base-classes10/com/govee/base2light/ble/comm/MultipleControllerCommV1.java:719-783,845-851
    (makeSendBytesV2 / makeWriteMultipleBytes(AbsMultipleControllerV2));
    split_h60ax/com/govee/pact_h60a0/ble/v1/SubModeMusicV1.java:97-99 (getWriteBytes = [19, 5, 100]).
    """
    value = bytes(effect)
    head = bytearray([0xA3, 0x00, 0x01, 0x00, 0x41, music_code & 0xFF])
    mids, tail = [], b""
    if len(value) <= 13:
        head += value
        count = 2
    else:
        head += value[:13]
        rest = value[13:]
        chunks = [rest[i : i + 17] for i in range(0, len(rest), 17)]
        mids = [append_bcc(bytes([0xA3, n & 0xFF]) + c) for n, c in enumerate(chunks[:-1], start=1)]
        tail = chunks[-1]
        count = len(chunks) + 1
    head[3] = count & 0xFF
    return [
        append_bcc(bytes(head)),
        *mids,
        append_bcc(bytes([0xA3, 0xFF]) + tail),
        append_bcc(bytes([0x33, 0x05, 0x13, 0x05, 0x64])),
    ]


def build_h61d3_light_recognition_fixed() -> list[bytes]:
    """Constant-only, unreachable in 7.7.02: the only 0xA3 V1 comm 0x5D value the app builds, H61D3Support.w0
    = Companion.a(100, -1, 300, 2, 2, 1, true, [#FF0000, #FFFF00, #00FF00, #0000FF, #00FFFF, #8B00FF]);
    never sent because ImageRecognitionActivity.e1 is only called with 1.
    split_h61d3/com/govee/h61d3/pact/H61D3Support.java:1627-1628 (w0);
    split_h61d3/com/govee/h61d3/ble/controller/LightRecognitionMultiController.java:35-67
    (Companion.a/<init>);
    base-classes10/com/govee/base2light/ble/comm/MultipleControllerCommV1.java:715-783
    (makeSendBytesV1/V2);
    split_h61d3/com/govee/h61d3/adjust/recognition/ImageRecognitionActivity.java:428-438,488-522 (N0/e1).
    """
    return [
        append_bcc(bytes.fromhex("a30001025d64ffffff2c010202010106ff0000")),
        append_bcc(bytes.fromhex("a3ffffff0000ff000000ff00ffff8b00ff")),
    ]


def build_hollowlamp_local_colors(entries: Sequence[tuple[int, int, int, int]]) -> list[bytes]:
    """0xA3 V1 comm 0xA5 hollowlamp (H6059) preset colours: value [n] + n x [R][G][B][kelvin u16be]; entries =
    [(r, g, b, kelvin)] with RGB = LocalColor.getShowColor() and kelvin = LocalColor.getKelvin() (0 unless
    a colour-temperature colour)
    (split_hollowlamp/com/govee/hollowlamp/ble/LocalColorSettingMultiControllerV1.java:14-32,38-44,52-54;
    LocalColorSettingAc.java:454-468).
    """
    v = [len(entries) & 0xFF]
    for r, g, b, k in entries:
        v += [r & 0xFF, g & 0xFF, b & 0xFF, (k >> 8) & 0xFF, k & 0xFF]
    return build_multi_a3_v1(0xA5, bytes(v))


def build_hollowlamp_multi_music_v1(
    effect: int,
    colors: Sequence[tuple[int, int, int]],
    sensitivity: int | None = None,
    rhythm_flag: int = 0,
) -> list[bytes]:
    """Legacy hollowlamp MultiMusicController 0xA3 V1 comm 0x41: value [effect][n][n x (R,G,B)] + (n-1) zero
    bytes (array n+1+3n), no frames when n == 0; with a SubModeMusicV3 (UiV5.v) the write result then sends
    33 05 13 [effect][flag][sens] 00
    (split_hollowlamp/com/govee/hollowlamp/ble/MultiMusicController.java:15-36,46-48,60-62;
    split_hollowlamp/com/govee/hollowlamp/adjust/BleOpV3.java:339-353;
    split_hollowlamp/com/govee/hollowlamp/ble/SubModeMusicV3.java:46-58,83-85). colors = [(r, g, b), ...].
    """
    n = len(colors)
    if n == 0:
        return []
    value: bytes | bytearray = bytearray([effect & 0xFF, n & 0xFF])
    for r, g, b in colors:
        value += bytes([r & 0xFF, g & 0xFF, b & 0xFF])
    value += bytes(max(0, (4 * n + 1) - len(value)))
    value = bytes(value)
    head = bytearray([0xA3, 0x00, 0x01, 0x00, 0x41])
    mids, tail = [], b""
    if len(value) <= 14:
        head += value
        count = 2
    else:
        head += value[:14]
        rest = value[14:]
        k = (len(rest) + 16) // 17
        for i in range(1, k + 1):
            chunk = rest[(i - 1) * 17 : i * 17]
            if i == k:
                tail = chunk
            else:
                mids.append(append_bcc(bytes([0xA3, i & 0xFF]) + chunk))
        count = k + 1
    head[3] = count & 0xFF
    frames = [append_bcc(bytes(head)), *mids, append_bcc(bytes([163, 255]) + tail)]
    if sensitivity is not None:
        frames.append(
            append_bcc(bytes([0x33, 0x05, 0x13, effect & 0xFF, rhythm_flag & 0xFF, sensitivity & 0xFF, 0x00]))
        )
    return frames


def build_multi_a3_v1_group(comm: int | Sequence[int], value: bytes, pro_type: int = 0xA3) -> list[bytes]:
    """0xA3 multi-packet write on the BLE GROUP path: MultipleControllerCommV1Group.k
    (base-classes10/com/govee/base2light/ble/comm/MultipleControllerCommV1Group.java:86-147; V1 p()!=0 via
    j :82-84, V2 comm=[cmd, p()] via l :149-155) reuses one 17-byte buffer (:117), so the END frame tail
    after the last chunk keeps stale bytes from the previous MIDDLE chunk. Reached via
    AbsMultipleBleCommGroup (:43, :190-197, :260-268). comm = one comm byte (int) or a sequence of comm
    bytes.
    """
    comm = bytes([comm & 0xFF]) if isinstance(comm, int) else bytes(comm)
    value = bytes(value)
    cap0 = 15 - len(comm)
    head = bytearray(19)
    head[0] = pro_type & 0xFF
    head[2] = 0x01
    head[4 : 4 + len(comm)] = comm
    end = bytearray(19)
    end[0] = pro_type & 0xFF
    end[1] = 0xFF
    mids = []
    if len(value) <= cap0:
        head[4 + len(comm) : 4 + len(comm) + len(value)] = value
        count = 2
    else:
        head[4 + len(comm) : 19] = value[:cap0]
        rest = value[cap0:]
        n = (len(rest) + 16) // 17
        buf = bytearray(17)
        pos = 0
        for i in range(1, n + 1):
            take = len(rest) - pos if i == n else 17
            buf[0:take] = rest[pos : pos + take]
            pos += take
            if i == n:
                end[2:19] = buf
            else:
                mids.append(append_bcc(bytes([pro_type & 0xFF, i & 0xFF]) + bytes(buf)))
        count = n + 1
    head[3] = count & 0xFF
    return [append_bcc(bytes(head)), *mids, append_bcc(bytes(end))]


def build_music_h1771(
    code: int,
    colors: Sequence[tuple[int, int, int]],
    sub_effects: Sequence[int],
    sensitivity: int,
    preview: bool = False,
) -> list[bytes]:
    """H1771 (goodsType 395) music: apply = A3 V2 [0x41, code] value [n][n x RGB][sub_effects] (code 4: [n][n
    x RGB][sub_effects[0]]) then 33 05 13 code sens; preview = 33 05 13 code sens only
    (split_hollowlamp/com/govee/hollowlamp/newdetails/h1771/H1771MusicMode.java:82-112 d;
    base-classes11/com/govee/base2light/mode/music/bean/MusicEffect.java:209-225 toBytes). colors = [(r, g,
    b), ...].
    """
    mode = append_bcc(bytes([0x33, 0x05, 0x13, code & 0xFF, sensitivity & 0xFF]))
    if preview:
        return [mode]
    value: bytes | bytearray = bytearray([len(colors) & 0xFF])
    for r, g, b in colors:
        value += bytes([r & 0xFF, g & 0xFF, b & 0xFF])
    if code == 4:
        cast(bytearray, value).append(sub_effects[0] & 0xFF)
    else:
        value += bytes(s & 0xFF for s in sub_effects)
    value = bytes(value)
    head = bytearray([0xA3, 0x00, 0x01, 0x00, 0x41, code & 0xFF])
    mids, tail = [], b""
    if len(value) <= 13:
        head += value
        count = 2
    else:
        head += value[:13]
        rest = value[13:]
        n = (len(rest) + 16) // 17
        for i in range(1, n + 1):
            chunk = rest[(i - 1) * 17 : i * 17]
            if i == n:
                tail = chunk
            else:
                mids.append(append_bcc(bytes([0xA3, i & 0xFF]) + chunk))
        count = n + 1
    head[3] = count & 0xFF
    return [append_bcc(bytes(head)), *mids, append_bcc(bytes([163, 255]) + tail), mode]


def build_music_h6057(
    code: int,
    colors: Sequence[tuple[int, int, int]],
    sub_effects: Sequence[int],
    sensitivity: int,
    preview: bool = False,
) -> list[bytes]:
    """H6057 (goodsType 104) music: apply = A3 V2 [0x41, code] value [n][n x RGB][sub_effects] then 33 05 13
    code sens; preview = 33 05 13 code sens only
    (split_hollowlamp/com/govee/h6057/origin/newDetail/H6057MusicMode.java:76-107 d;
    base-classes11/com/govee/base2light/mode/music/bean/MusicEffect.java:209-225 toBytes). colors = [(r, g,
    b), ...].
    """
    mode = append_bcc(bytes([0x33, 0x05, 0x13, code & 0xFF, sensitivity & 0xFF]))
    if preview:
        return [mode]
    value: bytes | bytearray = bytearray([len(colors) & 0xFF])
    for r, g, b in colors:
        value += bytes([r & 0xFF, g & 0xFF, b & 0xFF])
    value += bytes(s & 0xFF for s in sub_effects)
    value = bytes(value)
    head = bytearray([0xA3, 0x00, 0x01, 0x00, 0x41, code & 0xFF])
    mids, tail = [], b""
    if len(value) <= 13:
        head += value
        count = 2
    else:
        head += value[:13]
        rest = value[13:]
        n = (len(rest) + 16) // 17
        for i in range(1, n + 1):
            chunk = rest[(i - 1) * 17 : i * 17]
            if i == n:
                tail = chunk
            else:
                mids.append(append_bcc(bytes([0xA3, i & 0xFF]) + chunk))
        count = n + 1
    head[3] = count & 0xFF
    return [append_bcc(bytes(head)), *mids, append_bcc(bytes([163, 255]) + tail), mode]


def build_music_h6059(
    code: int,
    colors: Sequence[tuple[int, int, int]],
    sub_effects: Sequence[int],
    sensitivity: int,
    preview: bool = False,
) -> list[bytes]:
    """H6059 (goodsType 84) music: apply = A3 V2 [0x41, code] value [n][n x RGB][sub_effects] then 33 05 13
    code [sub_effects[0] if any] sens; preview (sensitivity change) = 33 05 13 code sub_effects... sens
    only (split_hollowlamp/com/govee/hollowlamp/newdetails/H6059MusicMode.java:67-89 d;
    base-classes11/com/govee/base2light/mode/music/bean/MusicEffect.java:209-225 toBytes;
    base-classes11/com/govee/base2light/pact/newdetail/content/music/AbsDevicePickSound.java:114-141).
    colors = [(r, g, b), ...].
    """
    if preview:
        ext = bytes([0x13, code & 0xFF] + [s & 0xFF for s in sub_effects] + [sensitivity & 0xFF])
        return [append_bcc(bytes([0x33, 0x05]) + ext[:17])]
    value: bytes | bytearray = bytearray([len(colors) & 0xFF])
    for r, g, b in colors:
        value += bytes([r & 0xFF, g & 0xFF, b & 0xFF])
    value += bytes(s & 0xFF for s in sub_effects)
    value = bytes(value)
    head = bytearray([0xA3, 0x00, 0x01, 0x00, 0x41, code & 0xFF])
    mids, tail = [], b""
    if len(value) <= 13:
        head += value
        count = 2
    else:
        head += value[:13]
        rest = value[13:]
        n = (len(rest) + 16) // 17
        for i in range(1, n + 1):
            chunk = rest[(i - 1) * 17 : i * 17]
            if i == n:
                tail = chunk
            else:
                mids.append(append_bcc(bytes([0xA3, i & 0xFF]) + chunk))
        count = n + 1
    head[3] = count & 0xFF
    frames = [append_bcc(bytes(head)), *mids, append_bcc(bytes([163, 255]) + tail)]
    mode = [0x13, code & 0xFF] + ([sub_effects[0] & 0xFF] if len(sub_effects) else []) + [sensitivity & 0xFF]
    return [*frames, append_bcc(bytes([51, 5, *mode]))]


def build_music_mode_compose_v2(music_code: int, sensitivity: int, effect: bytes) -> list[bytes]:
    """0xA3 V2 multi-packet, comm [0x41, music_code] + effect bytes, then 33 05 13 [music_code] [sensitivity]
    (MusicModeComposeController).
    base-classes11/com/govee/base2light/pact/newdetail/ble/controller/MusicModeComposeController.java:21-28
    (<init>: multi first, then mode frame);
    base-classes10/com/govee/base2light/ble/controller/MusicControllerNoEvent3Result.java:15-21 (<init>);
    base-classes10/com/govee/base2light/ble/controller/MultipleController4Music.java:14-18,38-46,55-58
    (<init>/g/getCommandType/p);
    base-classes10/com/govee/base2light/ble/comm/MultipleControllerCommV1.java:719-783,845-851
    (makeSendBytesV2/makeWriteMultipleBytes);
    base-classes11/com/govee/base2light/kt/general_controller/ControllerMode.java:62-65,102-105,111-117
    (Companion.e/<init>/getCommandType = 5); senders
    split_h60ax/com/govee/pact_h60a0/adjust/h60c1/H60C1MusicMode.java:78-118 (d) and
    split_h60ax/com/govee/pact_h60a0/adjust/h1270/music/MusicModeH1270.java:371-399 (d).
    """
    value = bytes(effect)
    head = bytearray([0xA3, 0x00, 0x01, 0x00, 0x41, music_code & 0xFF])
    mids, tail = [], b""
    if len(value) <= 13:
        head += value
        count = 2
    else:
        head += value[:13]
        rest = value[13:]
        chunks = [rest[i : i + 17] for i in range(0, len(rest), 17)]
        mids = [append_bcc(bytes([0xA3, n & 0xFF]) + c) for n, c in enumerate(chunks[:-1], start=1)]
        tail = chunks[-1]
        count = len(chunks) + 1
    head[3] = count & 0xFF
    return [
        append_bcc(bytes(head)),
        *mids,
        append_bcc(bytes([0xA3, 0xFF]) + tail),
        append_bcc(bytes([0x33, 0x05, 0x13, music_code & 0xFF, sensitivity & 0xFF])),
    ]


def build_music_multi_a3_v2(music_code: int, value: bytes) -> list[bytes]:
    """0xA3 V2 music-effect upload, comm [0x41, music_code]: START [A3 00 01 count 41 code value[0:13]],
    MIDDLE i [A3 i 17B], END [A3 FF <=17B]
    (base-classes10/com/govee/base2light/ble/controller/MultipleController4Music.java:14-18,39-45,56-57;
    base-classes10/com/govee/base2light/ble/controller/AbsMultipleControllerV2.java:19-20;
    base-classes10/com/govee/base2light/ble/comm/MultipleControllerCommV1.java:719-783,845-851).
    """
    value = bytes(value)
    head = bytearray([0xA3, 0x00, 0x01, 0x00, 0x41, music_code & 0xFF])
    mids, tail = [], b""
    if len(value) <= 13:
        head += value
        count = 2
    else:
        head += value[:13]
        rest = value[13:]
        n = (len(rest) + 16) // 17
        for i in range(1, n + 1):
            chunk = rest[(i - 1) * 17 : i * 17]
            if i == n:
                tail = chunk
            else:
                mids.append(append_bcc(bytes([0xA3, i & 0xFF]) + chunk))
        count = n + 1
    head[3] = count & 0xFF
    return [append_bcc(bytes(head)), *mids, append_bcc(bytes([163, 255]) + tail)]


def build_music_v2_then_mode(music_code: int, value: bytes, mode_payload: bytes) -> list[bytes]:
    """Control-screen music apply: the 0xA3 V2 [0x41, music_code] frames, then 33 05 [mode_payload] once the
    upload succeeds
    (base-classes11/com/govee/base2light/pact/newdetail/ble/controller/MusicModeComposeController.java:21-28;
    base-classes10/com/govee/base2light/ble/controller/AbsComposeControllerWithOpResult.java:88-104,315-343;
    base-classes11/com/govee/base2light/kt/general_controller/ControllerMode.java:62-65,102-105).
    """
    value = bytes(value)
    head = bytearray([0xA3, 0x00, 0x01, 0x00, 0x41, music_code & 0xFF])
    mids, tail = [], b""
    if len(value) <= 13:
        head += value
        count = 2
    else:
        head += value[:13]
        rest = value[13:]
        n = (len(rest) + 16) // 17
        for i in range(1, n + 1):
            chunk = rest[(i - 1) * 17 : i * 17]
            if i == n:
                tail = chunk
            else:
                mids.append(append_bcc(bytes([0xA3, i & 0xFF]) + chunk))
        count = n + 1
    head[3] = count & 0xFF
    frames = [append_bcc(bytes(head)), *mids, append_bcc(bytes([163, 255]) + tail)]
    return [*frames, append_bcc(bytes([51, 5]) + bytes(mode_payload)[:17])]


def build_set_ai_action(
    preview: bool,
    untraced_0: int,
    untraced_1: int,
    light_type: int,
    colors: Sequence[int],
    speed: int,
    loop_time: int,
) -> list[bytes]:
    """0xA3 V1 multi-packet, comm 0x58: AI-effect parameters [preview][len u16le = 3n+9][untraced_0
    u16le][untraced_1 u16le][light_type u16le][n][n x RGB][speed][loop_time]. Meaning of untraced_0 and
    untraced_1 untraced: the app copies them from the first two u16le of the payload of the AC 01 edit-info
    reply (the read_ai_edit_info request) and never interprets them
    (base-classes10/com/govee/base2light/ai4edit/AIEffectEditDialog.java:655-663).
    base-classes10/com/govee/base2light/ai4edit/controller/SetAiActionController.java:21-52 (c), :54-59 (d:
    makeSendBytesV1(0xA3, 88, value)), :62-64 (getCommandType = 88);
    base-classes10/com/govee/base2light/ai4edit/AIEffectEditDialog.java:706-711 (Y);
    base-classes10/com/govee/base2light/ble/comm/MultipleControllerCommV1.java:715-783.
    """
    n = len(colors)
    ln = n * 3 + 9
    value = bytearray(
        [
            1 if preview else 0,
            ln & 0xFF,
            (ln >> 8) & 0xFF,
            untraced_0 & 0xFF,
            (untraced_0 >> 8) & 0xFF,
            untraced_1 & 0xFF,
            (untraced_1 >> 8) & 0xFF,
            light_type & 0xFF,
            (light_type >> 8) & 0xFF,
            n & 0xFF,
        ]
    )
    for c in colors:
        value += bytes([(c >> 16) & 0xFF, (c >> 8) & 0xFF, c & 0xFF])
    value += bytes([speed & 0xFF, loop_time & 0xFF])
    head = bytearray([0xA3, 0x00, 0x01, 0x00, 0x58])
    tail: bytes | bytearray
    mids, tail = [], b""
    if len(value) <= 14:
        head += value
        count = 2
    else:
        head += value[:14]
        rest = value[14:]
        chunks = [rest[i : i + 17] for i in range(0, len(rest), 17)]
        mids = [append_bcc(bytes([0xA3, k & 0xFF]) + ch) for k, ch in enumerate(chunks[:-1], start=1)]
        tail = chunks[-1]
        count = len(chunks) + 1
    head[3] = count & 0xFF
    return [append_bcc(bytes(head)), *mids, append_bcc(bytes([0xA3, 0xFF]) + tail)]


def build_set_ai_action_v2(
    preview: bool,
    untraced_0: int,
    untraced_1: int,
    light_type: int,
    colors: Sequence[int],
    speed: int,
    loop_time: int,
    direction: int,
    gradient: int,
) -> list[bytes]:
    """0xA3 V1 multi-packet, comm 0x58, V2 value: V1 layout with len u16le = 3n+11 and [direction
    code][gradient] appended. untraced_0 and untraced_1 as in build_set_ai_action.
    base-classes10/com/govee/base2light/ai4edit/controller/SetAiActionControllerV2.java:15-31 (c);
    base-classes10/com/govee/base2light/ai4edit/controller/SetAiActionController.java:21-59 (c/d);
    base-classes10/com/govee/base2light/ai4edit/data/AiEditBean.java:109-130 (b: direction code 0-8);
    base-classes10/com/govee/base2light/ai4edit/AIEffectEditDialog.java:710 (V2 when g).
    """
    n = len(colors)
    ln = n * 3 + 11
    value = bytearray(
        [
            1 if preview else 0,
            ln & 0xFF,
            (ln >> 8) & 0xFF,
            untraced_0 & 0xFF,
            (untraced_0 >> 8) & 0xFF,
            untraced_1 & 0xFF,
            (untraced_1 >> 8) & 0xFF,
            light_type & 0xFF,
            (light_type >> 8) & 0xFF,
            n & 0xFF,
        ]
    )
    for c in colors:
        value += bytes([(c >> 16) & 0xFF, (c >> 8) & 0xFF, c & 0xFF])
    value += bytes([speed & 0xFF, loop_time & 0xFF, direction & 0xFF, gradient & 0xFF])
    head = bytearray([0xA3, 0x00, 0x01, 0x00, 0x58])
    tail: bytes | bytearray
    mids, tail = [], b""
    if len(value) <= 14:
        head += value
        count = 2
    else:
        head += value[:14]
        rest = value[14:]
        chunks = [rest[i : i + 17] for i in range(0, len(rest), 17)]
        mids = [append_bcc(bytes([0xA3, k & 0xFF]) + ch) for k, ch in enumerate(chunks[:-1], start=1)]
        tail = chunks[-1]
        count = len(chunks) + 1
    head[3] = count & 0xFF
    return [append_bcc(bytes(head)), *mids, append_bcc(bytes([0xA3, 0xFF]) + tail)]


def build_shape_layout_h6067(
    shapes: Sequence[tuple[int, int, int, int]],
    goods_type: int,
    ext: tuple[int, int] | None = None,
    states: Sequence[int] | None = None,
) -> list[bytes]:
    """0xA3 V1 comm 0x31 Glide Tri/Glide Y shape layout: value [n] + n x [type][x s16le][y s16le][angle
    s16le], + ext [powerNumber][powerEdgeNumber] of the last ext shape, + (goodsType 121 only) [00 00] + n
    x [state]; shapes = [(type, x, y, angle)] with x, y = ceil
    (split_h6061/com/govee/h6067/ble/MultiDeviceController.java:18-65,71-77; CalibrationDialog.java:306).
    """
    v = [len(shapes) & 0xFF]
    for t, x, y, a in shapes:
        v += [t & 0xFF, x & 0xFF, (x >> 8) & 0xFF, y & 0xFF, (y >> 8) & 0xFF, a & 0xFF, (a >> 8) & 0xFF]
    if ext is not None:
        v += [ext[0] & 0xFF, ext[1] & 0xFF]
    if goods_type == 121:
        v += [0, 0] + [s & 0xFF for s in (states or [0] * len(shapes))]
    return build_multi_a3_v1(0x31, bytes(v))


def build_shape_layout_h606a(positions: Sequence[tuple[int, int, int]], angle_offset: int = 0) -> list[bytes]:
    """0xA3 V1 comm 0x31 H606A/H6063/H6069 shape layout: value [positions[0].angle + angle_offset s16le][n] +
    n x [x s16le][y s16le]; positions = [(x, y, angle)] already rounded by the ctor used
    (split_h6061/com/govee/cubelight/ble/MultiDeviceControllerH606A.java:20-47 ceil, :49-76 Math.round,
    :152-158 g/getCommandType; CalibrationDialogH606A.java:254, CalibrationDialogH6069.java:306,
    RecognitionSolveH6069Ac.java:457).
    """
    n = len(positions)
    a = positions[0][2] + angle_offset
    v = [a & 0xFF, (a >> 8) & 0xFF, n & 0xFF]
    for x, y, _ in positions:
        v += [x & 0xFF, (x >> 8) & 0xFF, y & 0xFF, (y >> 8) & 0xFF]
    return build_multi_a3_v1(0x31, bytes(v))


def read_ai_edit_info(reset: bool, game_id: int, feature_id: int) -> bytes:
    """AC 01 [reset, gameId u16le, featureId u16le] AI-effect edit info read
    (base-classes10/com/govee/base2light/ai4edit/controller/ReadAiEditInfoController.java:53-65;
    AbsSingleReadMultiReplyController.java:17-20).
    """
    return append_bcc(
        bytes(
            [
                0xAC,
                0x01,
                0x01 if reset else 0x00,
                game_id & 0xFF,
                (game_id >> 8) & 0xFF,
                feature_id & 0xFF,
                (feature_id >> 8) & 0xFF,
            ]
        )
    )


def read_hollowlamp_local_colors(page: int = 1) -> bytes:
    """AA A5 [page] hollowlamp (H6059) preset-colour page read, 1-based, default 1; request bytes equal
    read_color_group(page), reply differs
    (split_hollowlamp/com/govee/hollowlamp/ble/LocalColorReadControllerV1.java:26-48,65-71;
    LocalColorSettingAc.java:413).
    """
    return read_request(0xA5, page)


def build_mtu_a4_frames(comm_byte: int, value: bytes, mtu: int = 20) -> list[bytes]:
    """0xA4 MTU frames [A4 seq u16le ...][XOR of preceding bytes], variable length, commByte at START byte 6
    (MultipleControllerCommV1.makeSendBytesMtu
    base-classes10/com/govee/base2light/ble/comm/MultipleControllerCommV1.java:366-493, via
    Compose4DefWrite4Multi.Companion.d
    base-classes11/com/govee/base2light/kt/ble/Compose4DefWrite4Multi.java:231-247).
    """

    def _x(b: bytes | bytearray) -> int:
        x = 0
        for v in b:
            x ^= v
        return x

    mtu = max(20, mtu)
    value = bytes(value)
    first, mid = mtu - 8, mtu - 4
    if len(value) <= first:
        s = bytes([0xA4, 0x00, 0x00, 0x01, 0x02, 0x00, comm_byte & 0xFF]) + value
        t = bytes([0xA4, 0xFF, 0xFF])
        return [s + bytes([_x(s)]), t + bytes([_x(t)])]
    rest = len(value) - first
    rem = rest % mid
    total = rest // mid + 1 + (1 if rem else 0)
    s = bytes([0xA4, 0x00, 0x00, 0x01, total & 0xFF, (total >> 8) & 0xFF, comm_byte & 0xFF]) + value[:first]
    frames = [s + bytes([_x(s)])]
    for i in range(1, total):
        off = first + (i - 1) * mid
        if i == total - 1:
            body = bytes([0xA4, 0xFF, 0xFF]) + value[off : off + (mid if rem == 0 else rem)]
        else:
            body = bytes([0xA4, i & 0xFF, (i >> 8) & 0xFF]) + value[off : off + mid]
        frames.append(body + bytes([_x(body)]))
    return frames


def build_remote_pair(on: bool) -> bytes:
    """0x33 A4 [on] remote pairing (Controller4RemotePair.Companion.d,
    base-classes11/com/govee/base2light/kt/general_controller/Controller4RemotePair.java:49-52,67-69;
    callers split_hollowlamp/com/govee/straightfloorlamp/adjust/VM4Light.java:1092,1098).
    """
    return append_bcc(bytes([0x33, 0xA4, 0x01 if on else 0x00]))


def read_remote_pair() -> bytes:
    """0xAA A4 remote pairing read, reply u8 (Controller4RemotePair.Companion.b / c,
    base-classes11/com/govee/base2light/kt/general_controller/Controller4RemotePair.java:30-47; caller
    split_hollowlamp/com/govee/straightfloorlamp/adjust/VM4Light.java:1082).
    """
    return read_request(0xA4)


def build_mic_color_cmd_mode(payload: bytes) -> bytes:
    """Mic pickup colour (pickUpMode 2) on the unified-config ControllerMode branch: 33 05 [colour payload], a
    20-byte XOR frame that MicColorCmdController sends verbatim (0xA5/0x90 are identity only).
    base-classes10/com/govee/base2light/ble/mic/controller/MicColorCmdController.java:18-21 (g);
    base-classes11/com/govee/base2light/pact/newdetail/content/mic/DelegateMicPickup.java:1084-1121 (x0);
    base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/ColorModeControllerConfig.java:215-220,237-243
    (Companion.c); base-classes11/com/govee/base2light/kt/ble/AbsController.java:42-60
    (getNextCommBytes/q).
    """
    return append_bcc(bytes([0x33, 0x05]) + bytes(payload)[:17])


def build_mic_color_cmd_over56(payload: bytes) -> bytes:
    """Mic pickup colour (pickUpMode 2) for supportColorModeOver56Segment SKUs: only the header frame [A3 00
    01 total 6E payload[0:14]] of the 0xA3/0x6E colour transfer is sent.
    base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/ColorModeControllerConfig.java:234-235,689-726
    (c/j); base-classes11/com/govee/base2light/kt/general_controller/MultiController.java:54-66;
    base-classes10/com/govee/base2light/ble/comm/MultipleControllerCommV1.java:715-783
    (makeSendBytesV1/V2);
    base-classes11/com/govee/base2light/pact/newdetail/content/mic/DelegateMicPickup.java:1118-1121
    (value.get(0)).
    """
    rest = len(payload) - 14
    total = 2 if rest <= 0 else (rest // 17 + (1 if rest % 17 else 0)) + 1
    return append_bcc(bytes([0xA3, 0x00, 0x01, total & 0xFF, 0x6E]) + bytes(payload[:14]))


def read_barelightv1_group_colors(part_brightness: bool, pieces: int) -> list[bytes]:
    """barelightv1 (H6145/H6146/H6147/H6171) on rgbiclight VM4Light: AA A5 g, 3 per group, when pactType >= 10
    and pactCode >= 1 (runs as goodsType 17, pieces 15) or, as goodsType 13, hard >= 1.00.01 and soft >=
    1.06.00; else AA A2 g, 4 per group (split_rgbiclight/com/govee/barelightv1/pact/Model.java:54-71;
    base-classes9/com/govee/base2home/pact/support/OldRgbicBkUtil.java:18-21,32,36,59-69;
    split_rgbiclight/com/govee/dreamcolorlightv1/pact/Support.java:986-1004,1713-1716;
    split_rgbiclight/com/govee/rgbiclight/Support4BareLightV1.java:37-51,63-65;
    split_rgbiclight/com/govee/rgbiclight/Support.java:98-114,197-209,216-229;
    split_rgbiclight/com/govee/rgbiclight/VM4Light.java:882-970).
    """
    size = 3 if part_brightness else 4
    cmd = 0xA5 if part_brightness else 0xA2
    groups = pieces // size + (1 if pieces % size else 0)
    return [read_request(cmd, g) for g in range(1, groups + 1)]


def read_h6119_group_colors(pieces: int) -> list[bytes]:
    """H6119 (goodsType 0, or 44 when OldCarBkUtil.isBkCar) on rgbiclight VM4Light: AA A5 g, 3 [bri,R,G,B] per
    group, g = 1..ceil(pieces/3); the connect read t5 is skipped below hard 1.00.03 / soft 1.06.00
    (Support.j) but the heartbeat path J3 -> Compose4Off2On -> G5 -> O5 -> N5 has no firmware gate
    (split_rgbiclight/com/govee/h6119/sku/Model.java:38-47;
    base-classes9/com/govee/base2home/pact/support/OldCarBkUtil.java:24-26;
    split_rgbiclight/com/govee/rgbiclight/VM4Light.java:275-287,650-668,732-760,882-970;
    split_rgbiclight/com/govee/rgbiclight/Support.java:98-114,278-283;
    split_rgbiclight/com/govee/dreamcolorlightv1/LightVersionServiceImpl.java:81-82).
    """
    groups = pieces // 3 + (1 if pieces % 3 else 0)
    return [read_request(0xA5, g) for g in range(1, groups + 1)]


def read_h612526_group_colors(part_brightness: bool, pieces: int = 15) -> list[bytes]:
    """H6125/H6126 on soft >= 1.03.01 (opened as goodsType 17 on rgbiclight VM4Light): AA A5 g, 3 per group,
    when pactType >= 10 and pactCode >= 1 or hard >= 1.00.03 and soft >= 1.06.00, else AA A2 g, 4 per
    group; pieces 15 unless the connect IC list fills info4BleIot.q()
    (split_rgbiclight/com/govee/h612526/sku/Model.java:56-75;
    base-classes9/com/govee/base2home/pact/support/OldDreamColorUtil.java:94-98;
    split_rgbiclight/com/govee/dreamcolorlightv1/pact/Support.java:670-692,986-1004,1713-1743;
    split_rgbiclight/com/govee/dreamcolorlightv1/LightVersionServiceImpl.java:76-91;
    split_rgbiclight/com/govee/rgbiclight/VM4Light.java:882-970).
    """
    size = 3 if part_brightness else 4
    cmd = 0xA5 if part_brightness else 0xA2
    groups = pieces // size + (1 if pieces % size else 0)
    return [read_request(cmd, g) for g in range(1, groups + 1)]


def read_hollowlamp_local_color_count() -> bytes:
    """AA A5 with no selector, H6059 control-screen connect read; reply [position][groupNum] -> preset-colour
    page count
    (split_hollowlamp/com/govee/hollowlamp/newdetails/H6059NewDetailVM$connectBleSuc$1$5$1.java:158-170,297-306;
    base-classes11/com/govee/base2light/kt/general_controller/Controller4ExtBytes.java:49-63).
    """
    return read_request(0xA5)


def read_rgbic_group_colors(part_brightness: bool, group_size: int, pieces: int) -> list[bytes]:
    """rgbiclight VM4Light.N5 group colour batch: AA A5 g if Support.D (part brightness) else AA A2 g, g =
    1..ceil(pieces/group_size); sent from connect t5, heartbeat switch change G5 -> O5, k5, W5 and IoT
    (split_rgbiclight/com/govee/rgbiclight/VM4Light.java:882-970,650-668,275-287,351-356,732-760;
    split_rgbiclight/com/govee/rgbiclight/Support.java:98-114,216-235).
    """
    cmd = 0xA5 if part_brightness else 0xA2
    groups = pieces // group_size + (1 if pieces % group_size else 0)
    return [read_request(cmd, g) for g in range(1, groups + 1)]


def build_auto_induction(on: bool) -> bytes:
    """Hollow/shape lamp auto-induction on/off [on] (OnOffAutoInductionController.q
    split_hollowlamp/com/govee/hollowlamp/ble/OnOffAutoInductionController.java:31-33; H6059NewDetailVM.O4
    Controller4ExtBytes.d([0xA6, on])
    split_hollowlamp/com/govee/hollowlamp/newdetails/H6059NewDetailVM.java:399).
    """
    return append_bcc(bytes([0x33, 0xA6, 0x01 if on else 0x00]))


def build_exit_remote_pair() -> bytes:
    """Straight floor lamp: exit remote-control pairing, 33 a6 with no payload (ExitRemoteController.q
    split_hollowlamp/com/govee/straightfloorlamp/ble/ExitRemoteController.java:24-26; caller
    split_hollowlamp/com/govee/straightfloorlamp/adjust/v1/RemoteControlAc.java:235).
    """
    return append_bcc(bytes([0x33, 0xA6]))


def build_mtu_a6_package(
    chunk: bytes,
    mtu: int,
    max_frames: int,
    verify8: Sequence[int],
    pkg_count: int = 1,
    pkg_index: int = 1,
) -> list[bytes]:
    """One 0xA6 / cmd 0x63 DaySync MTU big package as a list of variable-length frames, each ending in the XOR
    of its preceding bytes (DaySyncUtils.j/k/H
    base-classes11/com/govee/base2light/pact/newdetail/content/daySync/DaySyncUtils.java:926-949, 951-1018,
    264-297; sent as ControllerMTUMulti(frames, 0x63, proType 0xA6) by DaySyncUtils.w :1393-1407).
    """

    def _x(b: bytes | bytearray) -> int:
        x = 0
        for v in b:
            x ^= v
        return x

    chunk = bytes(chunk)
    cap0, capn = mtu - 18, mtu - 4
    if len(chunk) <= cap0:
        start = bytearray(len(chunk) + 18)
        start[0], start[3], start[16] = 0xA6, 0x01, 0x63
        start[17 : 17 + len(chunk)] = chunk
        end = bytes([0xA6, 0xFF, 0xFF])
        frames = [start, bytearray(end + bytes([_x(end)]))]
    else:
        start = bytearray(mtu)
        start[0], start[3], start[16] = 0xA6, 0x01, 0x63
        start[17 : 17 + cap0] = chunk[:cap0]
        frames, pos, i = [start], cap0, 1
        while i < max_frames:
            if i == max_frames - 1 or pos + capn >= len(chunk):
                e = bytes([0xA6, 0xFF, 0xFF]) + chunk[pos:]
                frames.append(bytearray(e + bytes([_x(e)])))
                break
            m = bytes([0xA6, i & 0xFF, (i >> 8) & 0xFF]) + chunk[pos : pos + capn]
            frames.append(bytearray(m + bytes([_x(m)])))
            pos += capn
            i += 1
    s = frames[0]
    s[4], s[5] = pkg_count & 0xFF, pkg_index & 0xFF
    s[6:14] = bytes(verify8)[:8]
    s[14], s[15] = len(frames) & 0xFF, (len(frames) >> 8) & 0xFF
    s[-1] = _x(s[:-1])
    return [bytes(f) for f in frames]


def build_position_calibration_h6062(flag: bool) -> bytes:
    """H6062 special-function position calibration [0x01, 0 if flag else 1]
    (Special4PositionCalibrationController.q
    split_h6062/com/govee/striplight/ble/Special4PositionCalibrationController.java:35-37; caller
    split_h6062/com/govee/striplight/add/v3/AcCalibrationPosition.java:237).
    """
    return append_bcc(bytes([0x33, 0xA6, 0x01, 0x00 if flag else 0x01]))


def read_logo() -> bytes:
    """0xAA A6 logo read, no selector; reply [opType][brightness][R][G][B] (LogoController(),
    base-classes10/com/govee/base2light/ble/controller/LogoController.java:12-14,23-29,42-47; callers
    split_h6047/com/govee/h6047/detail/NewDetailVm.java:756,1265).
    """
    return read_request(0xA6)


def read_logo_or_auto_induction() -> bytes:
    """Read 0xA6 with no selector: logo state on H6047, auto-induction on hollowlamp (LogoController() read
    ctor base-classes10/com/govee/base2light/ble/controller/LogoController.java:12-13;
    OnOffAutoInductionController() read ctor
    split_hollowlamp/com/govee/hollowlamp/ble/OnOffAutoInductionController.java:9-10;
    Controller4ExtBytes.b([0xA6])
    split_hollowlamp/com/govee/hollowlamp/newdetails/H6059NewDetailVM$connectBleSuc$1$5$1.java:308).
    """
    return read_request(0xA6)


def read_position_calibration_h6062() -> bytes:
    """H6062 position-calibration state read, aa a6 01 (Special4PositionCalibrationController.p
    split_h6062/com/govee/striplight/ble/Special4PositionCalibrationController.java:26-28; caller
    split_h6062/com/govee/striplight/add/v3/PairAc.java:134).
    """
    return read_request(0xA6, 0x01)


def build_hollowlamp_sensitivity(level: int) -> bytes:
    """0x33 A7 [sensitivity, UI range 1..30] - hollowlamp auto-induction sensitivity (SensitiveController.q,
    split_hollowlamp/com/govee/hollowlamp/ble/SensitiveController.java:13-16,22-33; H6059NewDetailVM.Q4
    Controller4ExtBytes [0xA7, i],
    split_hollowlamp/com/govee/hollowlamp/newdetails/H6059NewDetailVM.java:408-418).
    """
    return append_bcc(bytes([0x33, 0xA7, level & 0xFF]))


def build_sensitivity(level: int) -> bytes:
    """0x33 A7 [level] hollowlamp sensitivity (SensitiveController.q,
    split_hollowlamp/com/govee/hollowlamp/ble/SensitiveController.java:13-16,22-24,31-33; caller
    split_hollowlamp/com/govee/hollowlamp/adjust/UiV5.java:1337).
    """
    return append_bcc(bytes([0x33, 0xA7, level & 0xFF]))


def read_hollowlamp_sensitivity() -> bytes:
    """0xAA A7 - hollowlamp sensitivity read, reply validBytes[0] (SensitiveController.parseValidBytes,
    split_hollowlamp/com/govee/hollowlamp/ble/SensitiveController.java:9-11,26-29; H6059
    Controller4ExtBytes.Companion.b([0xA7]),
    split_hollowlamp/com/govee/hollowlamp/newdetails/H6059NewDetailVM$connectBleSuc$1$5$1.java:317).
    """
    return read_request(0xA7)


def read_sensitivity() -> bytes:
    """0xAA A7 hollowlamp sensitivity read, reply u8 (SensitiveController(),
    split_hollowlamp/com/govee/hollowlamp/ble/SensitiveController.java:9-11,26-28; connect read
    split_hollowlamp/com/govee/hollowlamp/adjust/BleOpV3.java:89).
    """
    return read_request(0xA7)


def build_ai_filter(
    on: bool,
    red: int,
    green: int,
    blue: int,
    saturation: int,
    filter_code: int,
    duration_minutes: int,
    year: int,
    month: int,
    day: int,
    hour: int,
    minute: int,
) -> bytes:
    """0x33 A9 10 0F [on, red, green, blue, saturation, filter_code u16le, duration_minutes u16le, year
    u16le, mon, day, hour, min] - AI filter: the filter's colour and saturation, its code, how long it runs,
    and the UTC time it ends (now + duration) (AiFilterController.a
    base-classes12/com/govee/base2light/videomode/newdetail/controller/AiFilterController.java:40-46;
    AiFilterVmInterface.java:65-67; field meanings from the filter-apply path
    base-classes12/com/govee/base2light/videomode/function/aifilter/AiFilterViewModel.java:273,279).
    """
    return append_bcc(
        bytes(
            [
                0x33,
                0xA9,
                0x10,
                0x0F,
                0x01 if on else 0x00,
                red & 0xFF,
                green & 0xFF,
                blue & 0xFF,
                saturation & 0xFF,
                filter_code & 0xFF,
                (filter_code >> 8) & 0xFF,
                duration_minutes & 0xFF,
                (duration_minutes >> 8) & 0xFF,
                year & 0xFF,
                (year >> 8) & 0xFF,
                month & 0xFF,
                day & 0xFF,
                hour & 0xFF,
                minute & 0xFF,
            ]
        )
    )


def build_ai_identify_model(model: int) -> bytes:
    """0x33 A9 08 02 [model u16le] - AI model select (ChangeAiModelController
    base-classes10/com/govee/base2light/ai4edit/controller/ChangeAiModelController.java:28-30;
    BleIotOp.java:181; AiIdentifyModelController.java:36-38).
    """
    return append_bcc(bytes([0x33, 0xA9, 0x08, 0x02, model & 0xFF, (model >> 8) & 0xFF]))


def build_ai_switch(on: bool) -> bytes:
    """0x33 A9 04 01 [on] - AI effect switch (Controller4AiSwitch
    base-classes10/com/govee/base2light/ai4edit/controller/Controller4AiSwitch.java:30-31,53-54;
    BleIotOp.java:260).
    """
    return append_bcc(bytes([0x33, 0xA9, 0x04, 0x01, 0x01 if on else 0x00]))


def build_black_border_remove(on: bool) -> bytes:
    """0x33 A9 0B 01 [on] - black-border removal (BlackBorderRemoveController.r
    base-classes12/com/govee/base2light/videomode/cotroller/BlackBorderRemoveController.java:41-45,56-57;
    BlackBorderVmInterface.java:33).
    """
    return append_bcc(bytes([0x33, 0xA9, 0x0B, 0x01, 0x01 if on else 0x00]))


def build_black_screen(enable: bool, short_mode: bool, short_s: int, long_s: int) -> bytes:
    """0x33 A9 0A 06 [en, 1|2, short u16le, long u16le] - no-signal auto-off (BlackScreenController.r
    base-classes10/com/govee/base2light/balckscreen/controller/BlackScreenController.java:59-71;
    BlackScreenViewModel.java:137).
    """
    return append_bcc(
        bytes(
            [
                0x33,
                0xA9,
                0x0A,
                0x06,
                0x01 if enable else 0x00,
                0x01 if short_mode else 0x02,
                short_s & 0xFF,
                (short_s >> 8) & 0xFF,
                long_s & 0xFF,
                (long_s >> 8) & 0xFF,
            ]
        )
    )


def build_black_screen_hdmi(enable: bool, duration: int) -> bytes:
    """0x33 A9 0C 03 [en, dur u16le] - HDMI no-signal auto-off (BlackScreenHdmiController.r
    base-classes10/com/govee/base2light/balckscreen/hdmi/controller/BlackScreenHdmiController.java:51-59;
    BlackScreenHdmiViewModel.java:130).
    """
    return append_bcc(
        bytes([0x33, 0xA9, 0x0C, 0x03, 0x01 if enable else 0x00, duration & 0xFF, (duration >> 8) & 0xFF])
    )


def build_hdmi_port(port: int) -> bytes:
    """0x33 A9 03 01 [port] - HDMI input select (HdmiActionParser.y
    base-classes13/com/govee/eco/executeAction/parser/HdmiActionParser.java:116-117).
    """
    return append_bcc(bytes([0x33, 0xA9, 0x03, 0x01, port & 0xFF]))


def build_hdr_cali() -> bytes:
    """0x33 A9 0D - start HDR calibration (HDRCaliController.b
    base-classes12/com/govee/base2light/videomode/newdetail/controller/HDRCaliController.java:34-35;
    HDRVmInterface.java:102-103).
    """
    return append_bcc(bytes([0x33, 0xA9, 0x0D]))


def build_video_auto_wb(value: int) -> bytes:
    """0x33 A9 12 01 [v] - send auto-WB calibration value (VideoAutoWbController.b
    base-classes12/com/govee/base2light/videomode/newdetail/controller/VideoAutoWbController.java:35-36;
    AutoCaliWbVm.java:272).
    """
    return append_bcc(bytes([0x33, 0xA9, 0x12, 0x01, value & 0xFF]))


def build_video_auto_wb_old() -> bytes:
    """0x33 A9 12 - legacy auto white balance, no data
    (WhiteBalanceVmInterface.getWhiteBalanceControllerAutoOld
    base-classes12/com/govee/base2light/videomode/newdetail/funtion/WhiteBalanceVmInterface.java:72-73).
    """
    return append_bcc(bytes([0x33, 0xA9, 0x12]))


def build_video_brightness(value: int) -> bytes:
    """0x33 A9 02 01 [v] - video-mode whole relative brightness
    (base-classes12/com/govee/base2light/videomode/newdetail/funtion/WholeRLBrightnessVmInterface.java:32).
    """
    return append_bcc(bytes([0x33, 0xA9, 0x02, 0x01, value & 0xFF]))


def build_video_color_cali(yellow: int, blue: int, purple: int, red: int | None = None) -> bytes:
    """0x33 A9 05 [3|4] [yellow, blue, purple(, red)] - colour calibration: the per-colour offset sliders
    (percent, 1-based); red is sent only when the device supports it (supportRedColor, default true)
    (ColorCaliController.a/b
    base-classes12/com/govee/base2light/videomode/newdetail/controller/ColorCaliController.java:30-37;
    ColorCaliVmInterface.java:34; slider order
    base-classes12/com/govee/base2light/videomode/newdetail/funtion/ColorCaliViewInterface.java:30-31,96-101,
    base-classes4/com/govee/ui/dialog/ColorCalibrationDialog.java:83-140,173-201 and slider names
    base-classes9/com/govee/base2home/databinding/ComponentColorCalibrationDialogBinding.java:124-153).
    """
    vals = [yellow, blue, purple] if red is None else [yellow, blue, purple, red]
    return append_bcc(bytes([0x33, 0xA9, 0x05, len(vals), *[v & 0xFF for v in vals]]))


def build_video_hdr_effect(on: bool, value: int) -> bytes:
    """0x33 A9 11 02 [on, value] - HDR effect/contrast (VideoHdrEffectController.a
    base-classes12/com/govee/base2light/videomode/newdetail/controller/VideoHdrEffectController.java:31-33;
    HDRContrastVmInterface.java:43).
    """
    return append_bcc(bytes([0x33, 0xA9, 0x11, 0x02, 0x01 if on else 0x00, value & 0xFF]))


def build_video_saturation(value: int) -> bytes:
    """0x33 A9 0E 01 [v] - video saturation (VideoSaturationController.a
    base-classes12/com/govee/base2light/videomode/newdetail/controller/VideoSaturationController.java:29-30;
    SaturationVmInterface.java:32).
    """
    return append_bcc(bytes([0x33, 0xA9, 0x0E, 0x01, value & 0xFF]))


def build_video_sensitivity(value: int) -> bytes:
    """0x33 A9 01 01 [v] - video sensitivity (VideoSensitivityController.a
    base-classes12/com/govee/base2light/videomode/newdetail/controller/VideoSensitivityController.java:29-30;
    SensitivityVmInterface.java:32).
    """
    return append_bcc(bytes([0x33, 0xA9, 0x01, 0x01, value & 0xFF]))


def build_video_wb_cali_exit() -> bytes:
    """0x33 A9 14 - exit WB calibration (VideoAutoWbController.a
    base-classes12/com/govee/base2light/videomode/newdetail/controller/VideoAutoWbController.java:29-30;
    AutoCaliWbVm.java:290).
    """
    return append_bcc(bytes([0x33, 0xA9, 0x14]))


def build_video_white_balance(auto: bool, r: int, b: int) -> bytes:
    """0x33 A9 00 03 [manual, r, b] - video white balance
    (WhiteBalanceVmInterface.getWhiteBalanceControllerNormal
    base-classes12/com/govee/base2light/videomode/newdetail/funtion/WhiteBalanceVmInterface.java:77-79,
    non-infinite branch).
    """
    return append_bcc(bytes([0x33, 0xA9, 0x00, 0x03, 0x00 if auto else 0x01, r & 0xFF, b & 0xFF]))


def build_video_white_balance_advanced(r: int, g: int, b: int) -> bytes:
    """0x33 A9 0F 03 [r, g, b] - advanced white balance
    (WhiteBalanceVmInterface.getWhiteBalanceControllerAdvanced
    base-classes12/com/govee/base2light/videomode/newdetail/funtion/WhiteBalanceVmInterface.java:66-68).
    """
    return append_bcc(bytes([0x33, 0xA9, 0x0F, 0x03, r & 0xFF, g & 0xFF, b & 0xFF]))


def build_video_white_balance_v1(progress: int) -> bytes:
    """0x33 A9 06 01 [progress] - white balance V1 (WhiteBalanceVmInterface.java:79 infinite-progress branch;
    same bytes compose/Controller4WhiteBalance.a
    base-classes10/com/govee/base2light/ble/controller/compose/Controller4WhiteBalance.java:31-32).
    """
    return append_bcc(bytes([0x33, 0xA9, 0x06, 0x01, progress & 0xFF]))


def read_ai_filter() -> bytes:
    """0xAA A9 10 - AI filter read (AiFilterController.c
    base-classes12/com/govee/base2light/videomode/newdetail/controller/AiFilterController.java:66-67; no
    caller in decomp).
    """
    return read_request(0xA9, 0x10)


def read_black_border_remove() -> bytes:
    """0xAA A9 0B - black-border read (BlackBorderRemoveController()
    base-classes12/com/govee/base2light/videomode/cotroller/BlackBorderRemoveController.java:36-39,52-53;
    no caller in decomp).
    """
    return read_request(0xA9, 0x0B)


def read_black_screen() -> bytes:
    """0xAA A9 0A - no-signal auto-off read (BlackScreenController.q
    base-classes10/com/govee/base2light/balckscreen/controller/BlackScreenController.java:55-56;
    BlackScreenViewModel.java:121).
    """
    return read_request(0xA9, 0x0A)


def read_black_screen_hdmi() -> bytes:
    """0xAA A9 0C - HDMI no-signal read (BlackScreenHdmiController.q
    base-classes10/com/govee/base2light/balckscreen/hdmi/controller/BlackScreenHdmiController.java:47-48;
    BlackScreenHdmiViewModel.java:116).
    """
    return read_request(0xA9, 0x0C)


def read_hdr_cali() -> bytes:
    """0xAA A9 0D - HDR calibration result read (HDRCaliController.a
    base-classes12/com/govee/base2light/videomode/newdetail/controller/HDRCaliController.java:28-29;
    HDRVmInterface.java:115-120).
    """
    return read_request(0xA9, 0x0D)


def read_video_brightness() -> bytes:
    """0xAA A9 02 - video brightness read (VideoModeParamsController(ParamsType.brightness).p
    base-classes10/com/govee/base2light/ble/controller/VideoModeParamsController.java:65-66,107-110;
    uncalled in decomp).
    """
    return read_request(0xA9, 0x02)


def read_video_check_wb_cali() -> bytes:
    """0xAA A9 13 - read whether WB is calibrated (VideoCheckWbCaliController.a
    base-classes12/com/govee/base2light/videomode/newdetail/controller/VideoCheckWbCaliController.java:30-31;
    no caller in decomp).
    """
    return read_request(0xA9, 0x13)


def read_video_color_cali() -> bytes:
    """0xAA A9 05 - colour-calibration read (ColorCaliController.d
    base-classes12/com/govee/base2light/videomode/newdetail/controller/ColorCaliController.java:49-50; no
    caller in decomp).
    """
    return read_request(0xA9, 0x05)


def read_video_hdr_effect() -> bytes:
    """0xAA A9 11 - HDR effect read (VideoHdrEffectController.c
    base-classes12/com/govee/base2light/videomode/newdetail/controller/VideoHdrEffectController.java:45-46;
    no caller in decomp).
    """
    return read_request(0xA9, 0x11)


def read_video_saturation() -> bytes:
    """0xAA A9 0E - saturation read (VideoSaturationController.c
    base-classes12/com/govee/base2light/videomode/newdetail/controller/VideoSaturationController.java:41-42;
    no caller in decomp).
    """
    return read_request(0xA9, 0x0E)


def read_video_sensitivity() -> bytes:
    """0xAA A9 01 - sensitivity read (VideoSensitivityController.c
    base-classes12/com/govee/base2light/videomode/newdetail/controller/VideoSensitivityController.java:41-42;
    no caller in decomp).
    """
    return read_request(0xA9, 0x01)


def read_video_white_balance() -> bytes:
    """0xAA A9 00 - white-balance read (VideoModeParamsController(ParamsType.whiteBalance).p
    base-classes10/com/govee/base2light/ble/controller/VideoModeParamsController.java:59-61,107-110;
    uncalled in decomp).
    """
    return read_request(0xA9, 0x00)


def read_video_white_balance_v1() -> bytes:
    """0xAA A9 06 - white balance V1 read (VideoModeParamsController(ParamsType.whiteBalanceV1).p
    base-classes10/com/govee/base2light/ble/controller/VideoModeParamsController.java:67-68,107-110;
    uncalled in decomp).
    """
    return read_request(0xA9, 0x06)


def build_check_light_install() -> bytes:
    """0x33 AA (empty payload) - enter check-light/install mode, no decomp caller
    (CheckLightInstallController,
    base-classes10/com/govee/base2light/ble/controller/compose/CheckLightInstallController.java:10-17;
    compose AbsController.getNextCommBytes :26-30).
    """
    return append_bcc(bytes([0x33, 0xAA]))


def build_ota_pact_report(pact_type: int, pact_code: int) -> bytes:
    """AB 02 [pactType u16le][pactCode]: OTA-pact report write on proType 0xAB, 20 bytes, XOR@19
    (ReportOtaPactController.<init>/getCommandType/getProType/q
    base-classes10/com/govee/base2light/ble/ota/auto/ReportOtaPactController.java:21-25,37-44,65-68;
    ReportOtaPactComposeController
    base-classes10/com/govee/base2light/ble/ota/auto/ReportOtaPactComposeController.java:18-22,29-53;
    hand-built split_plugnew/com/govee/h5080/adjust/newdetail/H5083NewDetailViewModel.java:214).
    """
    return append_bcc(bytes([0xAB, 0x02, pact_type & 0xFF, (pact_type >> 8) & 0xFF, pact_code & 0xFF]))


def build_plug_ota_pact_report(pact_type: int, pact_code: int) -> bytes:
    """AB 02 [pact_type u16le][pact_code u8]: plug OTA-pact report write
    (base-classes10/com/govee/base2light/ble/ota/auto/ReportOtaPactController.java:37-44,65-68;
    base-classes10/com/govee/base2light/ble/ota/auto/ReportOtaPactComposeController.java:28-53;
    split_plugnew/com/govee/h5080/adjust/newdetail/H5083NewDetailViewModel.java:206-214).
    """
    return append_bcc(bytes([0xAB, 0x02, pact_type & 0xFF, (pact_type >> 8) & 0xFF, pact_code & 0xFF]))


def read_dynamic_api_support() -> bytes:
    """AA AB, no selector: dynamic-API / hidden-SSID support read
    (base-classes10/com/govee/base2light/ble/controller/DynamicApiSupportController.java:7-30;
    DynamicApiSupportControllerV1.java:9; AbsSingleController.java:18-20,28-30,54-56).
    """
    return read_request(0xAB)


def read_wifi_func_list() -> bytes:
    """AB 03, no payload: Wi-Fi function list request, proType 0xAB
    (base-classes10/com/govee/base2light/ble/controller/Controller4ReadWifiFuncList.java:66-84;
    AbsController4WifiSingleSendMultiBack.java:25-28; AbsController4BleSingleSendMultiBack.java:156-160).
    """
    return append_bcc(bytes([0xAB, 0x03]))


def build_pick_mic(mic_source: int) -> bytes:
    """0x33 AC [mic_source] sound pickup select: 0 the device's own microphone, 1 the phone or headphone
    microphone (SoundEffectVmInterface.updatePickMic,
    base-classes12/com/govee/base2light/videomode/newdetail/funtion/SoundEffectVmInterface.java:38-45;
    source choice
    base-classes12/com/govee/base2light/videomode/newdetail/funtion/SoundEffectViewInterface$getSoundEffectView0$1$1.java:32-49).
    """
    return append_bcc(bytes([0x33, 0xAC, mic_source & 0xFF]))


def read_bluetooth_name() -> bytes:
    """0xAC 06 Bluetooth-name read with no ext bytes, sent identically by two classes that have no call site.
    base-classes11/com/govee/base2light/kt/general_controller/Controller4ReadBluetoothName.java:45-51 (ctor
    super(null), getCommandType 6) via
    base-classes10/com/govee/base2light/ble/controller/AbsController4BleSingleSendMultiBack.java:157-165;
    base-classes11/com/govee/base2light/kt/general_controller/Controller4ReadBluetoothNameMsg.java:131-142,201-218
    (makeReadController with no ext, getCommandType 6, p() = h) via
    base-classes10/com/govee/base2light/ble/controller/AbsSingleReadMultiReplyController.java:17-20 and
    base-classes10/com/govee/base2light/ble/controller/AbsSingleController.java:17-20.
    """
    return append_bcc(bytes([0xAC, 0x06]))


def read_combo_state() -> bytes:
    """0xAC 07 with no ext bytes (the default factory passes ext null); Controller4ReadComboState has no
    caller.
    base-classes11/com/govee/base2light/kt/general_controller/Controller4ReadComboState.java:131-142,201-218
    (makeReadController with no ext, ctor stores h, getCommandType 7, p() = h);
    base-classes10/com/govee/base2light/ble/controller/AbsSingleReadMultiReplyController.java:17-20
    (proType 0xAC); base-classes10/com/govee/base2light/ble/controller/AbsSingleController.java:17-20 (f =
    generate20Bytes(proType, cmd, p())).
    """
    return append_bcc(bytes([0xAC, 0x07]))


def read_cube_device_self() -> bytes:
    """AC 02 00 cube-light shape/connection self-check read
    (split_h6061/com/govee/cubelight/ble/DeviceSelfController.java:25-32;
    AbsSingleReadMultiReplyController.java:17-20).
    """
    return append_bcc(bytes([0xAC, 0x02, 0x00]))


def read_detail_info() -> bytes:
    """0xAC 03 with no ext bytes (no TLV count/type list); Controller4DetailInfo has no caller and no live
    path sends this frame.
    base-classes11/com/govee/base2light/kt/general_controller/Controller4DetailInfo.java:84-106
    (Companion.b, ctor super(null), getCommandType 3);
    base-classes10/com/govee/base2light/ble/controller/AbsController4BleSingleSendMultiBack.java:157-165
    (getNextCommBytes = BleUtils.p(0xAC, cmd, null));
    base-classes10/com/govee/base2kt/utils/BleUtils.java:1030-1041 (Companion.p).
    """
    return append_bcc(bytes([0xAC, 0x03]))


def read_h5086_recent_hour_chart() -> bytes:
    """AC 01 0D H5086 last-hour energy chart read
    (split_plugnew/com/govee/h5086/ble/controller/RecentOneHourChartController.java:65-67,150-156;
    AbsController4BleSingleSendMultiBack.java:157-165).
    """
    return append_bcc(bytes([0xAC, 0x01, 0x0D]))


def read_h5086_timers() -> bytes:
    """AC 01 13 H5086 timer-list read
    (split_plugnew/com/govee/h5086/ble/controller/TimerReadController.java:37-40,92-98;
    AbsController4BleSingleSendMultiBack.java:157-165).
    """
    return append_bcc(bytes([0xAC, 0x01, 0x13]))


def read_h6047_sound_way() -> bytes:
    """0xAA AC H6047 sound-way read, reply u8 (NewDetailVm$connectBleSuc$1,
    split_h6047/com/govee/h6047/detail/NewDetailVm$connectBleSuc$1.java:45-49,152-159).
    """
    return read_request(0xAC)


def read_sound_pickup() -> bytes:
    """0xAA AC (no selector): H6047 (119) connect-time sound pickup read, reply frame[2] 1 = headphone else
    device (NewDetailVm$connectBleSuc$1
    split_h6047/com/govee/h6047/detail/NewDetailVm$connectBleSuc$1.java:45-49,152-159; NewDetailVm.a5
    split_h6047/com/govee/h6047/detail/NewDetailVm.java:834-836).
    """
    return read_request(0xAC)


def read_status_color_h667172() -> bytes:
    """0xAC 03 status read of TLVs [0x40, 0x41, 0xA5] for H6671/H6672
    (split_rgbiclight/com/govee/rgbiclight/h667172/VM4LightH667172$afterConnectedSingleReadDeviceInfo$1.java:160;
    Compose4BaseInfoSingleRead.java:166-179).
    """
    return append_bcc(bytes([0xAC, 0x03, 0x03, 0x40, 0x41, 0xA5]))


def read_status_color_h703ab() -> bytes:
    """0xAC 03 status read of TLVs [0x41, 0xA5, 0x40, 0x16] for h61d3 goodsType 263
    (split_h61d3/com/govee/h61d3/adjustNew/VM4Light$afterConnectedSingleReadDeviceInfo$1.java:169;
    Compose4BaseInfoSingleRead.Companion.c
    base-classes11/com/govee/base2light/pact/newdetail/ble/controller/Compose4BaseInfoSingleRead.java:166-179).
    """
    return append_bcc(bytes([0xAC, 0x03, 0x04, 0x41, 0xA5, 0x40, 0x16]))


def read_status_color_rgbic_351() -> bytes:
    """0xAC 03 status read of TLVs [0x40, 0xA5] for rgbiclight goodsType 351/352
    (split_rgbiclight/com/govee/rgbiclight/VM4Light.java:720-725 D5, :1852-1855 x5;
    Compose4BaseInfoSingleRead.java:166-179).
    """
    return append_bcc(bytes([0xAC, 0x03, 0x02, 0x40, 0xA5]))


def read_status_h1771() -> bytes:
    """0xAC 03 status read of TLVs 0x41, 0x36, 0xA3, 0xA5 for H1771
    (H1771DetailVM$afterConnectedSingleReadDeviceInfo$1.invokeSuspend:180-212;
    Compose4BaseInfoSingleRead.Companion.c:167-180; ControllerSingleRead:80-83,93-95).
    """
    return append_bcc(bytes([0xAC, 0x03, 0x04, 0x41, 0x36, 0xA3, 0xA5]))


def read_status_h61d3_263() -> bytes:
    """0xAC 03 status read of TLVs 0x41, 0xA5, 0x40, 0x16 for h61d3 goodsType 263 (adjustNew
    VM4Light.d0:1065-1074 -> H61D3Support.l0:1340-1342 -> Y4 ->
    VM4Light$afterConnectedSingleReadDeviceInfo$1.invokeSuspend:163-199;
    Compose4BaseInfoSingleRead.Companion.c:167-180).
    """
    return append_bcc(bytes([0xAC, 0x03, 0x04, 0x41, 0xA5, 0x40, 0x16]))


def read_status_straightfloorlamp() -> bytes:
    """0xAC 03 status read of TLVs 0xA5 (colour group) + 0xA3 (gradual); sent only when goodsType 69 and
    pactType 2 (straightfloorlamp VM4Light.d0:816-827 -> Support.isH6076Iteration:171-173 -> Z4:290-296 ->
    VM4Light$afterConnectedSingleReadDeviceInfo$1.invokeSuspend:155-183;
    Compose4BaseInfoSingleRead.Companion.c:167-180; ControllerSingleRead:80-83,93-95;
    AbsController4BleSingleSendMultiBack.getNextCommBytes/getProType:158-165).
    """
    return append_bcc(bytes([0xAC, 0x03, 0x02, 0xA5, 0xA3]))


def read_relative_brightness() -> bytes:
    """0xAA AE 01 relative brightness read (RelativeBrightnessController.q,
    base-classes11/com/govee/base2light/relativebrightness/controller/RelativeBrightnessController.java:61-63,76-78,112-115).
    """
    return read_request(0xAE, 0x01)


def build_exit_check_light_install() -> bytes:
    """0x33 AF (empty payload) - exit check-light/install mode, no decomp caller
    (ExitCheckingInstallController,
    base-classes10/com/govee/base2light/ble/controller/compose/ExitCheckingInstallController.java:10-17).
    """
    return append_bcc(bytes([0x33, 0xAF]))


def build_exit_checking_install() -> bytes:
    """0x33 AF (no payload) exit install-check (ExitCheckingInstallController,
    base-classes10/com/govee/base2light/ble/controller/compose/ExitCheckingInstallController.java:10-17).
    No decompiled caller.
    """
    return append_bcc(bytes([0x33, 0xAF]))


def build_plug_delay(outlet_index: int, delay_type: int, minutes: int) -> bytes:
    """33 B0 [outlet, type (0 = delay-off, 1 = delay-on), minutes/60, minutes%60] (TurnOnOffDelayController.q
    split_plugnew/com/govee/h5080/ble/controller/TurnOnOffDelayController.java:22-28,104-107;
    H5083NewDetailViewModel.F0
    split_plugnew/com/govee/h5080/adjust/newdetail/H5083NewDetailViewModel.java:169-176).
    """
    return append_bcc(
        bytes(
            [
                0x33,
                0xB0,
                outlet_index & 0xFF,
                delay_type & 0xFF,
                (minutes // 60) & 0xFF,
                (minutes % 60) & 0xFF,
            ]
        )
    )


def build_plug_pro_delay(delay_on: bool, hours: int, minutes: int) -> bytes:
    """0x33 B0: H5086 countdown [type(0=off,1=on), hours, minutes], no outlet byte
    (DelayOnOffController.Companion.g/h,
    split_plugnew/com/govee/h5086/ble/controller/DelayOnOffController.java:71-79).
    """
    return append_bcc(bytes([0x33, 0xB0, 0x01 if delay_on else 0x00, hours & 0xFF, minutes & 0xFF]))


def read_plug_delay(outlet_index: int, delay_type: int) -> bytes:
    """0xAA B0 [outlet, type]: h5080 plug countdown read (TurnOnOffDelayController.p/parseValidBytes,
    split_plugnew/com/govee/h5080/ble/controller/TurnOnOffDelayController.java:90-102).
    """
    return read_request(0xB0, outlet_index, delay_type)


def read_plug_pro_delay(delay_on: bool) -> bytes:
    """0xAA B0 [type]: H5086 countdown read (DelayOnOffController.Companion.c/e,
    split_plugnew/com/govee/h5086/ble/controller/DelayOnOffController.java:47-69).
    """
    return read_request(0xB0, 0x01 if delay_on else 0x00)


def build_secret_write(secret: bytes) -> bytes:
    """33 B2 [base64-decoded secret]: account-lock unlock write sent after connect.
    base-classes10/com/govee/base2light/ble/controller/SecretKeyController.java:22-26,71-78;
    base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:110;
    split_plugnew/com/govee/h5086/ble/controller/SecretController.java:66-75,86-88;
    base-classes12/com/govee/base2light/vm4sku2op/Controller4Secret.java:28-46.
    The secret is the 8 bytes an AA B1 reply carried after its 0x01 selector, base64-encoded off-wire
    (SecretKeyController.parseValidBytes :57-62), so it decodes to 8 bytes. The one other length the app
    writes is 0: SecretKeyController stores "" for an empty secretCode (:24) and Base64.decode("") is empty,
    while an invalid one decodes to null (Encode.decryByBase64,
    base-classes10/com/govee/base2home/util/Encode.java:29-40); either way BleUtils.p writes no ext bytes
    (33 B2 00..). Any other length raises ValueError.
    """
    if len(secret) not in (0, 8):
        raise ValueError("secret must be 8 bytes (or empty: the app's no-secret frame)")
    return append_bcc(bytes([0x33, 0xB2]) + bytes(secret))


def read_secret() -> bytes:
    """AA B1 read of the 8-byte account-lock secret, no selector (pairing only).
    base-classes10/com/govee/base2light/ble/controller/SecretKeyController.java:13-20,46-49;
    base-classes10/com/govee/base2light/ble/controller/AbsSingleController.java:17-20,27-30;
    split_h61d3/com/govee/h61d3/ble/controller/SecretController.java:56-65,87-89;
    split_plugnew/com/govee/h5086/ble/controller/SecretController.java:55-64,86-88.
    """
    return read_request(0xB1)


def read_plug_spec() -> bytes:
    """AA B3, no selector: h5080 plug spec read, pairing batch only (SpecController
    split_plugnew/com/govee/h5080/ble/controller/SpecController.java:6-18; PairAcV1.t2
    split_plugnew/com/govee/h5080/add/PairAcV1.java:650-653).
    """
    return read_request(0xB3)


def build_plug_pro_timer(
    group: int, enable: bool, turn_on: bool, hour: int, minute: int, repeat: int
) -> bytes:
    """0x33 B4 [group, enableAndSwitch, hour, min, repeat] (h5086 TimerWriteController.Companion.a;
    H5083NewDetailViewModel.D0 :162).
    """
    eat = (0x80 if enable else 0x00) | (0x01 if turn_on else 0x00)
    return append_bcc(bytes([0x33, 0xB4, group & 0xFF, eat, hour & 0xFF, minute & 0xFF, repeat & 0xFF]))


def build_plug_timer(
    timer_index: int, outlet: int, enable: bool, turn_on: bool, hour: int, minute: int, repeat: int
) -> bytes:
    """33 B4 [index | 0x10 if outlet != 0, enableAndType, hour, minute, repeat] (NewTimerController.q
    split_plugnew/com/govee/h5080/ble/controller/NewTimerController.java:100-107;
    H5083NewDetailViewModel.D0
    split_plugnew/com/govee/h5080/adjust/newdetail/H5083NewDetailViewModel.java:160-167 = outlet 0).
    """
    slot = (timer_index & 0xFF) | (0x10 if outlet != 0 else 0x00)
    eat = (0x80 if enable else 0x00) | (0x01 if turn_on else 0x00)
    return append_bcc(bytes([0x33, 0xB4, slot, eat, hour & 0xFF, minute & 0xFF, repeat & 0xFF]))


def read_plug_timers(outlet: int) -> bytes:
    """AA B4 [1 if outlet == 1 else 0, FF]: read all four timers of an outlet (NewTimerController.p
    split_plugnew/com/govee/h5080/ble/controller/NewTimerController.java:67-69;
    H5083NewDetailRepository.necessaryMessages
    split_plugnew/com/govee/h5080/adjust/newdetail/H5083NewDetailRepository.java:332-334).
    """
    return read_request(0xB4, 0x01 if outlet == 1 else 0x00, 0xFF)


def build_plug_sync_time(unix_seconds: int, tz_hour: int, tz_min: int) -> bytes:
    """33 B5 [unix_seconds u32be][0x01][tz_hour s8][tz_min s8]: plug sync time
    (split_plugnew/com/govee/h5080/ble/controller/SyncTimeController.java:24-45;
    base-classes12/com/govee/base_h71xx/sku_base/ble/controller/SyncTimeController.java:20-41;
    split_plugnew/com/govee/h5086/ble/controller/SyncTimeController.java:24-27,38-40).
    """
    return append_bcc(
        bytes(
            [
                0x33,
                0xB5,
                (unix_seconds >> 24) & 0xFF,
                (unix_seconds >> 16) & 0xFF,
                (unix_seconds >> 8) & 0xFF,
                unix_seconds & 0xFF,
                0x01,
                tz_hour & 0xFF,
                tz_min & 0xFF,
            ]
        )
    )


def build_stop_ai_preview() -> bytes:
    """0x33 B8 01 00: stop the AI-action effect preview (StopPreViewAiActionController.getNextCommBytes
    base-classes10/com/govee/base2light/ai4edit/controller/StopPreViewAiActionController.java:17-26; caller
    AIEffectEditDialog.a0 base-classes10/com/govee/base2light/ai4edit/AIEffectEditDialog.java:743-771).
    """
    return append_bcc(bytes([0x33, 0xB8, 0x01, 0x00]))


def build_stop_preview_ai_action() -> bytes:
    """0x33 B8 [01, 00] stop AI preview (StopPreViewAiActionController.getNextCommBytes,
    base-classes10/com/govee/base2light/ai4edit/controller/StopPreViewAiActionController.java:13-26; caller
    base-classes10/com/govee/base2light/ai4edit/AIEffectEditDialog.java:747).
    """
    return append_bcc(bytes([0x33, 0xB8, 0x01, 0x00]))


def read_device_graphics() -> bytes:
    """0xAA B9 cube device-graphics read, gated on goodsType 228 (CubeVM4Light$connectBleSuc$2,
    split_h6061/com/govee/cubelight/newdetails/CubeVM4Light$connectBleSuc$2.java:149-157;
    Support.supportDeviceGraphics split_h6061/com/govee/cubelight/pact/Support.java:784-785).
    """
    return read_request(0xB9)


def read_device_graphics_md5() -> bytes:
    """0xAA B9: read the 16-byte MD5 of the device's stored shape layout (CubeVM4Light$connectBleSuc$2
    split_h6061/com/govee/cubelight/newdetails/CubeVM4Light$connectBleSuc$2.java:149-167; compared in
    CubeVM4Light.U4 split_h6061/com/govee/cubelight/newdetails/CubeVM4Light.java:605-619).
    """
    return read_request(0xB9)


def build_pair_process_complete() -> bytes:
    """0x3A BA 02: pair-process-complete notice on the h71xx/skubusiness channel
    (PairProcessCompleteExtKt.writePairProcessComplete
    base-classes4/com/govee/skubusiness/ext/PairProcessCompleteExtKt.java:45;
    GMessageSingleReadWrite.getSendBytes
    base-classes13/com/govee/ctlchannel/message/GMessageSingleReadWrite.java:35-37).
    """
    return append_bcc(bytes([0x3A, 0xBA, 0x02]))


def read_low_blue_light(scale: int = True) -> bytes:
    """0xAA BA [scale 1|0] low-blue-light read (ControllerLowBlueLight.p / u,
    base-classes11/com/govee/base2light/lowBlueLightControl/ControllerLowBlueLight.java:50-56,66-69,91-97;
    caller split_rgbiclight/com/govee/dreamcolorlightv1/adjust/v2/UiV3.java:326).
    """
    return read_request(0xBA, 0x01 if scale else 0x00)


def build_recognition_start(value: int) -> bytes:
    """33 ED 00 [value u16le]: start the video-recording light effect (app sends 280).
    split_h61d3/com/govee/h61d3/ble/controller/VideoRecognitionController.java:29-33,36-42 (Companion.b,
    BleUtils.D(i,false) LE; cmd BleProtocol.m = -19); caller
    split_h61d3/com/govee/h61d3/adjust/recognition/VideoActivity.java:457-464 (T0).
    """
    return append_bcc(bytes([0x33, 0xED, 0x00, value & 0xFF, (value >> 8) & 0xFF]))


def build_recognition_stop() -> bytes:
    """33 ED 01: close the recognition light effect.
    split_h61d3/com/govee/h61d3/ble/controller/VideoRecognitionController.java:24-27,36-42 (Companion.a);
    callers split_h61d3/com/govee/h61d3/adjust/recognition/VideoActivity.java:425 (P0), :593;
    split_h61d3/com/govee/h61d3/adjust/recognition/ImageRecognitionActivity$sendCloseLightEffectController$1.java:56.
    """
    return append_bcc(bytes([0x33, 0xED, 0x01]))


def build_video_recognition_effect(value: int) -> bytes:
    """33 ED 00 [value u16le]: video-recording light effect (VideoRecognitionController.Companion.b
    split_h61d3/com/govee/h61d3/ble/controller/VideoRecognitionController.java:29-33; VideoActivity.T0
    :457-464 sends 280).
    """
    return append_bcc(bytes([0x33, 0xED, 0x00, value & 0xFF, (value >> 8) & 0xFF]))


def build_video_recognition_stop() -> bytes:
    """33 ED 01: close the recognition light effect (VideoRecognitionController.Companion.a
    split_h61d3/com/govee/h61d3/ble/controller/VideoRecognitionController.java:24-27,36-42).
    """
    return append_bcc(bytes([0x33, 0xED, 0x01]))


def build_ota_prepare() -> bytes:
    """0x33 EE: enter OTA mode, no payload (OtaPrepareController.getCommandType/q,
    split_rgbiclight/com/govee/dreamcolorlightv1/ble/OtaPrepareController.java:11-17; kt
    Controller4ExtBytes.Companion.d([-18]),
    base-classes11/com/govee/base2light/kt/general_controller/Controller4ExtBytes.java:85-100).
    """
    return append_bcc(bytes([0x33, 0xEE]))


def read_pact() -> bytes:
    """0xAA EF protocol pact read, reply [pactType u16 BE][pactCode] (PactController,
    base-classes10/com/govee/base2light/ble/controller/PactController.java:9-20,27-41; caller
    base-classes4/com/govee/skubusiness/chain/pact/BlePactChecker.java:265).
    """
    return read_request(0xEF)


def build_daysync_after_bind() -> bytes:
    """33 05 F0 FF sent after bind on daysync devices
    (base-classes10/com/govee/base2light/connect/constant/DaySyncAfterConnectConfig.java:23-33;
    base-classes11/com/govee/base2light/kt/general_controller/Controller4ExtBytes.java:120-124).
    """
    return append_bcc(bytes([0x33, 0x05, 0xF0, 0xFF]))


def build_daysync_mode(
    op: int, scheme_id: int = 0, repeat_frequency: int = 0, enable_type: int | None = None
) -> bytes:
    """0x33 05 F0 daysync mode op 0-4 (DaySyncUtils.G,
    base-classes11/com/govee/base2light/pact/newdetail/content/daySync/DaySyncUtils.java:214-262 ->
    ControllerMode.Companion.e
    base-classes11/com/govee/base2light/kt/general_controller/ControllerMode.java:62-65,102-105).
    """
    if op == 0:
        ext = [0xF0, 0x00]
    elif op == 1:
        if enable_type is None:
            ext = [0xF0]
        elif enable_type == 1:
            ext = [0xF0, 0x01, 0xFF, 0x01, 30, 0x00]
        else:
            ext = [0xF0, 0x01, 0xFF, enable_type & 0xFF]
    elif op in (2, 3):
        ext = [0xF0, op, scheme_id & 0xFF]
    elif op == 4:
        ext = [0xF0, 0x04, scheme_id & 0xFF, repeat_frequency & 0xFF]
    else:
        ext = [0xF0]
    return append_bcc(bytes([51, 5, *ext]))


def build_daysync_refresh_city() -> bytes:
    """0x33 F0 (empty) daysync refresh city (DaySyncVM.refreshCity4Device,
    base-classes11/com/govee/base2light/pact/newdetail/content/daySync/DaySyncVM.java:3301;
    ControllerSingle.c
    base-classes11/com/govee/base2light/kt/general_controller/ControllerSingle.java:37-41).
    """
    return append_bcc(bytes([0x33, 0xF0]))


def read_daysync_running_node() -> bytes:
    """0xAA F0 daysync running-node read (ControllerSingle.a(0xF0) in DaySyncVM,
    base-classes11/com/govee/base2light/pact/newdetail/content/daySync/DaySyncVM.java:312;
    base-classes11/com/govee/base2light/kt/general_controller/ControllerSingle.java:26-29).
    """
    return read_request(0xF0)


def read_daysync_running() -> bytes:
    """0xAA F1 is-daysync-running read, gated on DaySyncConfig.y(info)[2] (ControllerSingle.a(0xF1) in
    DaySyncVM, base-classes11/com/govee/base2light/pact/newdetail/content/daySync/DaySyncVM.java:301-304).
    """
    return read_request(0xF1)


def build_brightness(level: int) -> bytes:
    """0x33 04 — brightness: one byte, the level's low 8 bits, as the app's per-SKU range already maps it.
    base-classes11/com/govee/base2light/kt/general_controller/ControllerBrightness.java:51-53,61-63,73-75
    (Companion.d);
    base-classes10/com/govee/base2light/ble/controller/BrightnessController.java:14-18,26-28,38-40 (q);
    split_h60ax/com/govee/pact_h60a0/ble/controller/compose/BrightnessControllerNoEvent.java:23-25,42-44
    (Companion.a).
    """
    return append_bcc(bytes([0x33, 0x04, level & 0xFF]))


def build_check_ic(check_type: int, ic_pos: int = 0, channel: int = -1, cmd: int = 0x46) -> bytes:
    """0x33 46|5A [check_type][channel, omitted when < 0][icPos u16be] IC cut / strip-length calibration
    (dreamcolorlightv1 CheckIcController.q
    split_rgbiclight/com/govee/dreamcolorlightv1/ble/CheckIcController.java:23-30; rgbiclight
    Compose4CutCaliIc split_rgbiclight/com/govee/rgbiclight/Compose4CutCaliIc.java:54-58; h61d3
    CheckIcController.Companion.a
    split_h61d3/com/govee/h61d3/ble/controller/CheckIcController.java:32-35,46-48; VM4LightH667172.T4
    split_rgbiclight/com/govee/rgbiclight/h667172/VM4LightH667172.java:538-562;
    CaliLastIcController.Companion.a/b/c/e/f
    base-classes10/com/govee/base2light/caliStripLen/CaliLastIcController.java:68-106,118-120 with cmd from
    LightStripLenCaliConfig.c
    base-classes10/com/govee/base2light/caliStripLen/LightStripLenCaliConfig.java:47-59).
    CAUTION, firmware-sourced: with cmd 0x46 this frame factory-resets H60A1/H60A6 (their dispatcher runs
    the gateway-config and Matter reset, then reboots, on any 33 46 frame whatever the payload;
    H60A6_ble_dispatcher.c:454-468, H60A1_ble_dispatcher.c:389-400). Never send it to them.
    """
    mid = [channel & 0xFF] if channel >= 0 else []
    return append_bcc(bytes([0x33, cmd & 0xFF, check_type & 0xFF, *mid, (ic_pos >> 8) & 0xFF, ic_pos & 0xFF]))


def build_combination_effect_value(
    segment_count: int, groups: Sequence[tuple[Sequence[int], bytes]]
) -> bytes:
    """DIY combination-effect 0x62 value (DiyCombEffect.r): [00][distinctEffects][segLen] + per effect
    [LSB-first mask][0A][00 00][u16le len][effect[1:]]
    (base-classes10/com/govee/base2light/ac/diyNew/views/combination/DiyCombEffect.java:537-584
    (DiyCombEffect.r), :420-460 (DiyCombEffect.k), :462-512 (DiyCombEffect.m), :645-651 (DiyCombEffect.u),
    :310-393 (DiyCombEffect.g); DiyAdvancedCombParse.java:276-282 (DiyAdvancedCombParse.toBytes)).
    """
    seg_len = 1 if segment_count <= 0 else (segment_count + 7) // 8
    seg_effect = {}
    for segments, effect in groups:
        for s in segments:
            if 0 <= s < segment_count:
                seg_effect[s] = bytes(effect)
    order = []
    for _, effect in groups:
        e = bytes(effect)
        if e not in order and e in seg_effect.values():
            order.append(e)
    out = bytearray([0x00, len(order) & 0xFF, seg_len & 0xFF])
    for e in order:
        mask = bytearray(seg_len)
        for s, v in seg_effect.items():
            if v == e:
                mask[s // 8] |= 1 << (s % 8)
        payload = e[1:]
        out += mask + bytes([0x0A, 0x00, 0x00, len(payload) & 0xFF, (len(payload) >> 8) & 0xFF]) + payload
    return bytes(out)


def build_daysync_scheme_upload(
    value: bytes,
    nodes: Sequence[bytes],
    mtu: int,
    device_kb: int,
    verify8: bytes | bytearray | Sequence[bytes],
) -> list[list[bytes]]:
    """DaySync scheme upload, cmd 0x63 over 0xA6 MTU multi-packet frames; returns a list of big packages (each
    a list of frames), [] when nodes is empty or a node does not fit.
    base-classes11/com/govee/base2light/pact/newdetail/content/daySync/DaySyncUtils.java:1393-1407
    (DaySyncUtils.w) -> :586-681 (R), :926-949 (j), :951-1018 (k), :264-297 (H); sent by
    ControllerMTUMulti(pkg, 0x63, 0xA6)
    base-classes11/com/govee/base2light/kt/general_controller/ControllerMTUMulti.java:17-34.
    """
    if not nodes:
        return []

    def _x(b: bytes | bytearray) -> int:
        x = 0
        for v in b:
            x ^= v
        return x

    def unsplit_package(data: bytes) -> list[bytes | bytearray]:
        s = bytearray(len(data) + 18)
        s[0] = 0xA6
        s[3] = 0x01
        s[16] = 0x63
        s[17 : 17 + len(data)] = data
        e = bytes([0xA6, 0xFF, 0xFF])
        return [s, e + bytes([_x(e)])]

    def split_package(data: bytes, cap0: int, capn: int, maxpk: int) -> list[bytes | bytearray]:
        if len(data) <= cap0:
            return unsplit_package(data)
        s = bytearray(mtu)
        s[0] = 0xA6
        s[3] = 0x01
        s[16] = 0x63
        s[17 : 17 + cap0] = data[:cap0]
        out: list[bytes | bytearray] = [s]
        pos, i = cap0, 1
        while i < maxpk:
            if i == maxpk - 1 or pos + capn >= len(data):
                e = bytes([0xA6, 0xFF, 0xFF]) + bytes(data[pos:])
                out.append(e + bytes([_x(e)]))
                break
            m = bytes([0xA6, i & 0xFF, (i >> 8) & 0xFF]) + bytes(data[pos : pos + capn])
            out.append(m + bytes([_x(m)]))
            pos += capn
            i += 1
        return out

    maxpk = (device_kb * 1024) // mtu
    cap0 = mtu - 18
    capn = mtu - 4
    big = []
    if cap0 >= len(value):
        big.append(unsplit_package(bytes(value)))
    else:
        cap_big = cap0 + (maxpk - 1) * capn
        acc, used = bytearray(value[:4]), 0
        for idx, node in enumerate(nodes):
            if idx == 0:
                used = 4
            if used + len(node) > cap_big:
                if idx == 0:
                    return []
                big.append(split_package(bytes(acc), cap0, capn, maxpk))
                acc, used = bytearray(), 0
            if used == 0 and len(node) > cap_big:
                return []
            acc += bytes(node)
            used += len(node)
            if idx == len(nodes) - 1:
                big.append(split_package(bytes(acc), cap0, capn, maxpk))
    per_pkg = not isinstance(verify8, (bytes, bytearray))
    for n, pk in enumerate(big):
        s = cast(bytearray, pk[0])
        s[4] = len(big) & 0xFF
        s[5] = (n + 1) & 0xFF
        s[6:14] = bytes(verify8[n] if per_pkg else verify8)[:8]  # type: ignore[arg-type]  # per_pkg selects verify8[n] (bytes); verify8 is a flat byte sequence otherwise
        s[14] = len(pk) & 0xFF
        s[15] = (len(pk) >> 8) & 0xFF
        s[-1] = _x(s[:-1])
    return [[bytes(f) for f in pk] for pk in big]


def build_logo(manual: bool, brightness: int = 0, r: int = 0, g: int = 0, b: int = 0) -> bytes:
    """Logo light 0x33 A6: manual colour -> [01, brightness, R, G, B]; auto -> [00] only (LogoController.q
    base-classes10/com/govee/base2light/ble/controller/LogoController.java:51-59; H6047 caller
    split_h6047/com/govee/h6047/detail/SettingAcNewDetail.java:348).
    """
    if not manual:
        return append_bcc(bytes([0x33, 0xA6, 0x00]))
    return append_bcc(bytes([0x33, 0xA6, 0x01, brightness & 0xFF, r & 0xFF, g & 0xFF, b & 0xFF]))


def build_low_blue_light(
    on: bool, level: int, *, scale: bool = True, red: int = 100, green: int = 100
) -> bytes:
    """0x33 BA [scale 1|0, on, red, green, level]: low-blue-light / eye-protection write. The three value
    bytes are the red, green and blue channels (the app's own names for them in its result type,
    ControllerEyeshadow.java:234-235); level is the blue one. Every app writer sends [1, on, 100, 100, level]
    (ControllerLowBlueLight.q
    base-classes11/com/govee/base2light/lowBlueLightControl/ControllerLowBlueLight.java:40-48,62-64,81-84;
    ControllerEyeshadow.Companion.f/g
    base-classes11/com/govee/base2light/kt/general_controller/ControllerEyeshadow.java:53-61,99-107,263-265).
    """
    return append_bcc(
        bytes(
            [
                0x33,
                0xBA,
                0x01 if scale else 0x00,
                0x01 if on else 0x00,
                red & 0xFF,
                green & 0xFF,
                level & 0xFF,
            ]
        )
    )


def build_music_code_sens(
    code: int, sensitivity: int, sub_effect0: int | None = None, *, sub_mode: int = 0x13
) -> bytes:
    """33 05 <sub_mode> <code> <sens> [sub_effect0] short music form (sub_mode 0x13 new-music default;
    0x11/0x0C/0x0E/0x0F/0x01 per family). Mode wrapper:
    base-classes11/com/govee/base2light/kt/general_controller/ControllerMode.java:62-64,102-104
    (Companion.e, cmd 5); base-classes10/com/govee/base2light/ble/controller/AbsModeController.java:30-31
    (cmd 5). 0x13: base-classes11/com/govee/base2light/mode/music/SubModeNewMusic.java:79-80
    (getWriteBytes);
    base-classes11/com/govee/base2light/pact/newdetail/commom/music/ComposeMusicMode.java:63-72 (d; 4th
    byte = subEffectList[0] when non-empty);
    base-classes11/com/govee/base2light/pact/newdetail/config/fuc/MusicConfig.java:136-137 (Companion.c;
    sub via Config.d 0->0x13, 1->0x11 :312-315);
    split_hollowlamp/com/govee/tablelampv1/ble/v2/SubModeMusicV2.java:51-52,81-82;
    split_h61d3/com/govee/h61d3/adjustNew/music/MusicMode.java:233-244 (d short branch; sub
    BleProtocol.f=19); split_rgbiclight/com/govee/rgbiclight/mode/music/MusicMode.java:97,137-148 (d; sub =
    Protocols.b: 0x13, 0x11 or 0x0C); split_h60ax/com/govee/pact_h60a0/ble/v1/SubModeMusicV1.java:97-98
    (constant code 5 / sens 100) sent by
    split_h60ax/com/govee/pact_h60a0/adjust/v1/CommOpV1.java:1643-1650,1993-2009 via
    split_h60ax/com/govee/pact_h60a0/ble/controller/compose/NewMusicMultipleController.java:19-29;
    split_h6047/com/govee/h6047/newdetail/music/H6039MusicMode.java:158,191-202;
    split_rgblight/com/govee/stringlightv2/ble/SubModeMusicMultiV2.java:90-92,184-185;
    split_rgbiclight/com/govee/dreamcolorlightv1/ble/SubModeMusicV3.java:107-109,196-197;
    split_hollowlamp/com/govee/h6057/origin/newDetail/H6057MusicMode.java:76-89;
    split_hollowlamp/com/govee/tablelampv1/newAdjust/H6078MusicMode.java:76-90;
    split_hollowlamp/com/govee/straightfloorlamp/MusicMode4H60B2.java:54-68. 0x11:
    split_rgbiclight/com/govee/dreamcolorlightv1/ble/SubModeMusic.java:78-81,172 (code 16; emitter
    split_rgbiclight/com/govee/dreamcolorlightv1/adjust/ui/MusicFragment.java:58-63);
    split_rgbiclight/com/govee/dreamcolorlightv1/ble/SubModeMusicV2.java:106-108,126,213 (new-music flag or
    code 16); split_rgbiclight/com/govee/rgbiclight/mode/music/MusicMode4DreamColorLightV2.java:107-119.
    0x0C: split_rgbiclight/com/govee/rgbiclight/Protocols.java:65-66,108-122 (d, codes != 2 -> [code, sens,
    f()]; emitter split_rgbiclight/com/govee/rgbiclight/mode/music/OldDevicePickSound.java:168);
    split_rgbiclight/com/govee/rgbiclight/mode/music/MusicMode4BareLightV1.java:89-101;
    split_rgbiclight/com/govee/h612526/newdetail/H612526MusicMode.java:69-71,77-78 (m with subEffect0 == 0,
    n). 0x0F: split_hollowlamp/com/govee/straightfloorlamp/MusicModeV1.java:75-86;
    split_hollowlamp/com/govee/straightfloorlamp/MusicMode4H16C0.java:74-86. 0x0E:
    split_rgblight/com/govee/h613839/ble/SubModeMusicV2.java:86-88,172-173;
    split_rgblight/com/govee/h6159/ble/SubModeMusicV2.java:92-94,178-179;
    split_rgblight/com/govee/stringlightv2/ble/SubModeMusicMultiV1.java:92-94,191-192;
    split_rgblight/com/govee/rgblight/mode/music/MusicModeV14StringLightV2.java:84-95. Same bytes, no write
    emitter found (parse-only, GAP): split_rgbiclight/com/govee/barelightv1/ble/SubModeMusic.java:72-75,166
    (0x0C code 0); split_rgbiclight/com/govee/h6119/ble/SubModeMusic.java:64-70,142 (0x0C codes != 2; code
    1 adds c); split_rgblight/com/govee/h7318/adjust/SubModeMusic.java:41-42,63-64 and
    split_bulblightstringv1/com/govee/h7022/adjust/mode/SubModeMusic.java:36-37,66-67 (0x01).
    """
    body = [0x33, 0x05, sub_mode, code, sensitivity]
    if sub_effect0 is not None:
        body.append(sub_effect0)
    return append_bcc(bytes(b & 0xFF for b in body))


def build_relative_brightness(
    segment6: bool, left: int, top: int, right: int, bottom: int, strip_left: int = 0, strip_right: int = 0
) -> bytes:
    """0x33 AE 01 [06 SEGMENT6 | 04 SEGMENT4] left top right bottom strip_left strip_right - relative
    (per-side) brightness write; all 6 value bytes are always sent
    (RelativeBrightnessController.r/getNextCommBytes,
    base-classes11/com/govee/base2light/relativebrightness/controller/RelativeBrightnessController.java:66-70,81-115;
    side order RelativeBrightnessBean.java:50-101 + RelativeBrightness6SegmentDialog.java:54-82,197-218).
    """
    return append_bcc(
        bytes(
            [
                0x33,
                0xAE,
                0x01,
                0x06 if segment6 else 0x04,
                left & 0xFF,
                top & 0xFF,
                right & 0xFF,
                bottom & 0xFF,
                strip_left & 0xFF,
                strip_right & 0xFF,
            ]
        )
    )


def build_scene(effect: int, music_code: int = 0) -> bytes:
    """0x33 05 04 — activate scene: [effect u2le][music_code u2le] (pass music_code only when the scene's
    white-noise switch is on; the app sends 0 otherwise).
    base-classes11/com/govee/base2light/pact/newdetail/content/scene/SubModeScenes.java:59-77
    (getWriteBytes, e=false);
    base-classes11/com/govee/base2light/pact/newdetail/content/scene/BaseSceneViewMode.java:722-740 (Z2),
    :923 (k3).
    """
    return append_bcc(
        bytes(
            [
                0x33,
                0x05,
                0x04,
                effect & 0xFF,
                (effect >> 8) & 0xFF,
                music_code & 0xFF,
                (music_code >> 8) & 0xFF,
            ]
        )
    )


def build_scene_cube(effect: int, direction: int, speed: int, point: bytes = b"") -> bytes:
    """0x33 05 04 — cube/panel scene: [effect u2le][direction u8][point 0 or 4 bytes][speed u8].
    split_h6061/com/govee/cubelight/ble/SubModeScenes.java:90-118 (getWriteBytes);
    split_h6061/com/govee/cubelight/newdetails/scene/CubeSceneViewModeV2.java:580-587,607,650 (k3).
    """
    if len(point) not in (0, 4):
        raise ValueError("point must be 0 or 4 bytes")
    return append_bcc(
        bytes([0x33, 0x05, 0x04, effect & 0xFF, (effect >> 8) & 0xFF, direction & 0xFF, *point, speed & 0xFF])
    )


def build_scene_h61d3(effect: int, direction: int = 0, speeds: Sequence[int] | None = None) -> bytes:
    """0x33 05 04 — H61D3 (goodsType 202) speed-type-1 scene: [effect u2le][dir pair 2B][N][speeds x N] (no
    count byte when speeds is None). split_h61d3/com/govee/h61d3/adjust/scene/SubModeScenes.java:101-113
    (getWriteBytes); split_h61d3/com/govee/h61d3/adjust/newdetail/scene/SceneViewModelH61d3.java:217-223
    (k3); base-classes10/com/govee/base2light/ac/diy/v1/h61d3/SubEffect4LiuDong.java:137-185 (Companion.b).
    """
    pair = {
        0: (0, 1),
        1: (0, 0),
        2: (1, 1),
        3: (1, 0),
        4: (1, 3),
        5: (0, 3),
        6: (1, 2),
        7: (0, 2),
        8: (0, 7),
        9: (0, 6),
        10: (0, 9),
        11: (0, 8),
        12: (0, 4),
        13: (0, 5),
    }.get(direction & 0xFF, (0xFF, 0xFF))
    body = [0x33, 0x05, 0x04, effect & 0xFF, (effect >> 8) & 0xFF, *pair]
    if speeds is not None:
        body += [len(speeds) & 0xFF, *[s & 0xFF for s in speeds]]
    return append_bcc(bytes(body))


def build_scene_with_speed(effect: int, speed: int = 100) -> bytes:
    """0x33 05 04 — bulblightstringv2 (H7031 family) scene: [effect u2le][speed u8], speed defaults to 100.
    split_bulblightstringv1/com/govee/bulblightstringv2/newAdjust/h7031/sceneMode/H7031SceneViewModel.java:99-119
    (k3); split_bulblightstringv1/com/govee/bulblightstringv2/ble/SubModeScenes.java:31-34 (getWriteBytes).
    """
    return append_bcc(bytes([0x33, 0x05, 0x04, effect & 0xFF, (effect >> 8) & 0xFF, speed & 0xFF]))


def build_screen_bind_success() -> bytes:
    """0x33 BA [01]: constant screen-bind-success notice sent once after pairing (pairing path, not control
    screen). ScreenBindSuccessController.getCommandType/q:
    base-classes10/com/govee/base2light/ble/ScreenBindSuccessController.java:29-37
    (BleProtocolConstants.SINGLE_RED_BLUE_LIGHT = -70:
    base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:153; proType 0x33 via
    AbsOnlyWriteSingleController super(true):
    base-classes10/com/govee/base2light/ble/controller/AbsOnlyWriteSingleController.java:41-42,
    AbsSingleController.getProType/g:
    base-classes10/com/govee/base2light/ble/controller/AbsSingleController.java:22-30). Sent by
    PairActivity.d2 -> y2 for goodsType 306/317/321:
    base-classes10/com/govee/base2light/connect/ui/PairActivity.java:330-332,667-669 (AnonymousClass1.a ->
    StepExecuteCommand.h :114-123).
    """
    return append_bcc(bytes([0x33, 0xBA, 0x01]))


def build_status_request(types: list[int], *, command: int = 0x03) -> bytes:
    """0xAC status read [0xAC, command, N, *types]; reply is a 0xAC burst of TLVs.
    base-classes11/com/govee/base2light/pact/newdetail/ble/controller/Compose4BaseInfoSingleRead.java:166-180,230
    (Companion.c prepends N; a ControllerSingleRead on that ext);
    base-classes11/com/govee/base2light/kt/general_controller/ControllerSingleRead.java:80-83,93-95 (cmd
    3);
    base-classes10/com/govee/base2light/ble/controller/AbsController4BleSingleSendMultiBack.java:158-165
    (BleUtils.p(0xAC, cmd, ext)); callers: split_h6047/com/govee/h6047/newdetail/h6038/VM4H6038.java:525
    (H6038 [41,30,36,A5]),
    split_h60ax/com/govee/pact_h60a0/adjust/h60a6/VM4LightH60A6$afterConnectedSingleReadDeviceInfo$1.java:158-160
    (H60A6 [41,30](+A5 if J5())), .../h1270/VM4LightH1270$afterConnectedSingleReadDeviceInfo$1.java:149,
    .../h1250/VM4LightH1250$afterConnectedSingleReadDeviceInfo$1.java:184,
    .../h12d0/VM4LightH12D0$afterConnectedSingleReadDeviceInfo$1.java:150 ([41,30,A5]).
    """
    return append_bcc(bytes([0xAC, command & 0xFF, len(types), *[t & 0xFF for t in types]]))


def build_switch(on: bool) -> bytes:
    """0x33 01 [on: 1/0]: power on/off, boolean byte (not the h5080 relay mask, see build_plug_relay).
    base2light SwitchController.q/getCommandType
    (base-classes10/com/govee/base2light/ble/controller/SwitchController.java:13-16,23-26,37-39; frame via
    AbsSingleController.g/getProType :22-30), e.g. dreamcolorlightv1 UiV1.onOffChange
    (split_rgbiclight/com/govee/dreamcolorlightv1/adjust/v1/UiV1.java:1328), h6129
    AdjustAc.onEventHeaderClick (split_rgbiclight/com/govee/h6129/adjust/AdjustAc.java:542); H5086
    SwitchController.Companion.d
    (split_plugnew/com/govee/h5086/ble/controller/SwitchController.java:45-48,59-61) written by
    SmartPlugProAdjustViewModel.m5
    (split_plugnew/com/govee/h5086/adjust/SmartPlugProAdjustViewModel.java:1375) and
    SmartRoomOp.makeSwitchOpBytes (split_plugnew/com/govee/h5086/scenes/SmartRoomOp.java:67-71).
    """
    return append_bcc(bytes([0x33, 0x01, 0x01 if on else 0x00]))


def build_sync_time(hour: int, minute: int, second: int, week: int, tz_hour: int, tz_min: int) -> bytes:
    """0x33 09 — short sync time [hour, minute, second, week 1=Mon..7=Sun, 0x01, tz_hour s8, tz_min s8].
    base-classes11/com/govee/base2light/kt/general_controller/ControllerSyncTime.java:37-41,71-73
    (Companion.b, getCommandType);
    base-classes10/com/govee/base2light/ble/controller/SyncTimeController.java:27-29,32-39 (getCommandType,
    q); split_h60ax/com/govee/pact_h60a0/ble/controller/compose/SyncTimeController.java:11-19 (<init>,
    getCommandType).
    """
    return append_bcc(bytes([0x33, 0x09, hour, minute, second, week, 0x01, tz_hour & 0xFF, tz_min & 0xFF]))


def build_worry_free_at_night(
    on: bool,
    open_hour: int,
    open_min: int,
    close_hour: int,
    close_min: int,
    sensitivity: int,
    brightness: int,
) -> bytes:
    """H6057 worry-free-at-night 0x36: [open, openH, openM, closeH, closeM, sensitivity(bean 'progress'),
    brightness] (H6057SettingActivity.K4 TAG_NIGHT_MODE
    split_hollowlamp/com/govee/h6057/origin/newDetail/H6057SettingActivity.java:841-850 via J4 :271-273;
    bytes WorryFreeAtNightBean.o
    base-classes10/com/govee/base2light/ble/controller/WorryFreeAtNightBean.java:90-92).
    """
    return append_bcc(
        bytes(
            [
                0x33,
                0x36,
                0x01 if on else 0x00,
                open_hour & 0xFF,
                open_min & 0xFF,
                close_hour & 0xFF,
                close_min & 0xFF,
                sensitivity & 0xFF,
                brightness & 0xFF,
            ]
        )
    )


def read_bar_switch() -> bytes:
    return read_request(0x36)


def read_device_basic_info() -> bytes:
    """Read basic info (AA 07 10); reply [0x10][uid 8 B][ble sw 3 B][ble hw 3 B]. Same frame as
    read_device_info(0x10). H5086 control screen:
    split_plugnew/com/govee/h5086/adjust/SmartPlugProAdjustViewModel.java:637,878 (t3 -> C4(true) ->
    ReadInfoController) -> split_plugnew/com/govee/h5086/ble/controller/ReadInfoController.java:62,64 ->
    DeviceBasicInfoController.java:113,116 (ext {ControllerProtocol.f=16}, cmd 7, proType 0xAA via
    base-classes10/com/govee/base2light/ble/controller/AbsControllerNoEvent4Single.java:140-144). Same
    bytes, no caller: base-classes10/com/govee/base2light/ble/controller/BasicInfoController.java:28,35;
    base-classes12/com/govee/base_h71xx/sku_base/ble/compose/VersionUuidInfoController.java:116,119;
    base-classes4/com/govee/skubusiness/repo/BaseSkuRepo.java:290-293 (BaseSkuRepo.e).
    """
    return read_request(0x07, 0x10)


def read_device_info(selector: int) -> bytes:
    """0xAA 07 [selector] device-info read; selector picks the field (0x02 SN, 0x03 hw ver, 0x07 DSP ver, 0x0A
    MCU sw ver, 0x0B MCU hw ver, 0x10 basic info, 0x11 basic wifi info).
    base-classes10/com/govee/base2light/ble/controller/McuSoftVersionControllerV1.java:26-28,32-34
    (McuSoftVersionControllerV1.getCommandType 7 / p [0x0a])
    base-classes10/com/govee/base2light/ble/controller/McuHardVersionControllerV1.java:26-28,32-34
    (McuHardVersionControllerV1.getCommandType 7 / p [0x0b])
    base-classes10/com/govee/base2light/ble/controller/AbsOnlyReadSingleController.java:5-7 +
    AbsSingleController.java:18-19,28-29 + AbsController.java:77 (read -> f() -> proType 0xAA,
    BleUtils.generate20Bytes)
    base-classes10/com/govee/base2kt/utils/BleUtils.java:1376-1378,1030-1041,1260-1267
    (BleUtils.Companion.p / w: zero-pad, XOR 0..18 at 19)
    base-classes10/com/govee/base2light/ble/controller/SnController.java:11-14,32-34,37-38 (0x02);
    HardVersionController.java:9-11,23-24,28-29 (0x03); DspVersionInDeviceInfoController.java:15-16,20-21
    (0x07); BasicInfoController.java:28-29,34-35 (0x10); BasicWifiInfoController.java:36-37,42-43 (0x11)
    0x0A/0x0B only via
    base-classes10/com/govee/base2light/connect/factory/ReadControllerFactory.java:31-33,50-51 (type 3) <-
    connect/step/StepReadInfo.java:211,295-298 (add-device connect path, not control screen)
    base-classes12/com/govee/base_h71xx/sku_base/ble/controller/McuSoftVersionController.java:18-25 /
    McuHardVersionController.java:18-25 (same bytes via BleProtocolConstants.java:146-148; never
    instantiated)
    """
    return read_request(0x07, selector)


def read_device_wifi_info() -> bytes:
    """0xAA 07 11 Wi-Fi info read (MAC + Wi-Fi sw/hw); reply [0x11][mac 6 B][wifi sw 3 B][wifi hw 3 B].
    Controllers: split_plugnew/com/govee/h5086/ble/controller/WifiInfoController.java:66-71
    (WifiInfoController.<init>/getCommandType, read via AbsMustController4Read; sent from H5086 pairing
    PairToDeviceInfoReadController.java:245);
    base-classes10/com/govee/base2light/ble/controller/BasicWifiInfoController.java:36-43
    (getCommandType/p; no call site); base-classes4/com/govee/skubusiness/repo/BaseSkuRepo.java:296-299
    (BaseSkuRepo.f -> GMessageSingleRead(7, [0x11]); no caller).
    """
    return read_request(0x07, 0x11)


def read_gradual() -> bytes:
    return read_request(0xA3)


def read_ic_segment() -> bytes:
    """0xAA 40 IC/segment-count read request, no selector (payload null -> zero-padded). Controllers:
    base-classes11/com/govee/base2light/kt/general_controller/ControllerIcNum.java:173-175
    (ControllerIcNum.getCommandType; read ctor (false,null) in Companion.b/c/d :78-138), frame built by
    base-classes11/com/govee/base2light/kt/ble/AbsController.java:43-45 ->
    base-classes10/com/govee/base2kt/utils/BleUtils.java:1030-1041;
    base-classes10/com/govee/base2light/ble/controller/IcNumController.java:32-34
    (IcNumController.getCommandType; read via AbsOnlyReadSingleController.java:4-7 /
    AbsSingleController.java:17-20,54-56, p()=null);
    split_rgbiclight/com/govee/dreamcolorlightv1/ble/ReadIcController.java:26-28 (SINGLE_READ_IC = 64,
    BleProtocol.java:13); split_h61d3/com/govee/h61d3/ble/controller/ReadIcController.java:114-136
    (Companion.e / getCommandType -> BleProtocol.r = 64);
    split_h6062/com/govee/striplight/ble/IcAndPieceController.java:16-17 (SINGLE_IC_NUM_AND_PIECE_NUM =
    64); split_h60ax/com/govee/pact_h60a0/ble/controller/compose/ControllerIcNum.java:28-40 (ctor
    (false,null), getCommandType).
    """
    return read_request(0x40)


def read_mode() -> bytes:
    """0xAA 05 01 mode read (selector 0x01 = govee_ble_frame mode_query.selector): legacy
    AbsModeController.p [01]
    (base-classes10/com/govee/base2light/ble/controller/AbsModeController.java:34-37);
    kt ControllerMode.Companion.b / .c default ext [01]
    (base-classes11/com/govee/base2light/kt/general_controller/ControllerMode.java:45-58,107-109), e.g. the
    H6057 newdetail connect read
    (split_hollowlamp/com/govee/h6057/origin/newDetail/controller/Compose4InfoBleIotH6057$iControllerStrategy$1.java:165).
    """
    return read_request(0x05, 0x01)  # selector 0x01 = request


def read_soft_version() -> bytes:
    return read_request(0x06)


def read_switch() -> bytes:
    """0xAA 01 — read power/relay state, also the cmd-1 keep-alive heartbeat (no selector; reply parsing
    varies by family). All emit BleUtils.Companion.p(0xAA, 0x01, null)
    (base-classes10/com/govee/base2kt/utils/BleUtils.java:1030-1041) via AbsSingleController.f with p()
    null (base-classes10/com/govee/base2light/ble/controller/AbsSingleController.java:18-20,28-30,54-56) or
    kt AbsController.getNextCommBytes with no ext
    (base-classes11/com/govee/base2light/kt/ble/AbsController.java:43-45): SwitchController read form
    (base-classes10/com/govee/base2light/ble/controller/SwitchController.java:9-11,24-26);
    H5080/H5083/H5085 goodsType 43 detail connect batch
    (split_plugnew/com/govee/h5080/adjust/newdetail/H5083NewDetailRepository.java:322-324)
    SwitchControllerV2 read
    (split_plugnew/com/govee/h5080/ble/controller/SwitchControllerV2.java:11-13,25-27,29-45); sent at
    split_plugnew/com/govee/h5080/adjust/v1/BleOpV1.java:155,499, adjust/v2/BleOpV2.java:157,519,
    adjust/h5089/BleOp4H5089.java:621 h5080 HeartControllerV2
    (split_plugnew/com/govee/h5080/ble/controller/HeartControllerV2.java:17-35;
    split_plugnew/com/govee/h5080/ble/BleComm.java:15) H5086 SwitchController.Companion.b
    (split_plugnew/com/govee/h5086/ble/controller/SwitchController.java:26-43,59-61;
    split_plugnew/com/govee/h5086/ble/controller/ReadInfoController.java:92-96) HeartController
    (base-classes10/com/govee/base2light/ble/controller/HeartController.java:18-21,31-38;
    AbsOnlyReadSingleController.java:4-12; AbsBleComm default keep-alive
    base-classes10/com/govee/base2light/ble/comm/AbsBleComm.java:59,77,97,226-228; HeartFactory.java:16-18)
    HeartControllerV2 (base-classes10/com/govee/base2light/ble/controller/HeartControllerV2.java:4-21;
    legacy BleComm.l in split_h6062/com/govee/striplight/ble/BleComm.java:13,23-28,
    split_rgbiclight/com/govee/dreamcolorlightv1/ble/BleComm.java:12,22-23,
    split_hollowlamp/com/govee/straightfloorlamp/ble/BleComm.java:12,22-23,
    split_h6061/com/govee/cubelight/ble/BleComm.java:14-15,
    split_h6061/com/govee/h6067/ble/BleComm.java:14-15) HeartControllerLamp
    (base-classes10/com/govee/base2light/ble/controller/HeartControllerLamp.java:118-121,131-158;
    split_hollowlamp/com/govee/hollowlamp/ble/BleComm.java:13-15; HeartFactory.java:20-21 /
    StepBleConnect.java:80 pairing;
    split_hollowlamp/com/govee/hollowlamp/pact/bleiot/AbsEffectOp4BleIot.java:222-224,
    pact/ble/AbsEffectOp4Ble.java:229) Controller4HeartCommon(ConnectHeartbeat.c(1) = 0x01)
    (base-classes11/com/govee/base2light/kt/ble/Controller4HeartCommon.java:13-22;
    base-classes12/com/govee/common2light/capability/connect/ConnectHeartbeat.java:26-38; new-detail
    base-classes11/com/govee/base2light/pact/newdetail/ble/BleComm.java:37-47,76-84) super.l()
    HeartController in split_h60ax/com/govee/pact_h60a0/ble/BleComm.java:21-22,
    split_rgbiclight/com/govee/h7090/ble/BleComm.java:21-22 H6101 CalibrationOkController
    (split_rgblight/com/govee/h6101/ble/CalibrationOkController.java:6-18;
    split_rgblight/com/govee/h6101/adjust/CalibrationAc.java:54-66,250-264) no caller in 7.7.02:
    HeartControllerWithBattery.java:71-74,84-112, kt Controller4HeartByOld.java:15-21,
    MainSwitchController.java:11-19,38-48
    """
    return read_request(0x01)


# ── self-checks: each builder's bytes vs the app layout it was traced from ──
# Evaluated at import; govee_reference._selftest reports the roll-up. Kept byte-exact so a
# regression in any builder fails the repo gate.
def _raises(exc: type[BaseException], fn: Callable[[], object]) -> bool:
    """True if ``fn()`` raises ``exc`` (a self-check helper for builders that must refuse an input)."""
    try:
        fn()
    except exc:
        return True
    return False


def _check(name: str, fn: Callable[[], object]) -> tuple[str, bool]:
    """Evaluate one builder self-check; a builder that raises counts as a failed check,
    never a crashed import.
    """
    try:
        return (name, bool(fn()))
    except Exception:  # noqa: BLE001 - a self-check must categorise any error as a failure
        return (name, False)


_SELF_CHECKS: list[tuple[str, bool]] = [
    _check(
        "read_connect_heartbeat",
        lambda: bool(
            read_connect_heartbeat(0) == read_heartbeat()
            and read_connect_heartbeat(2) == read_heartbeat()
            and read_connect_heartbeat(7) == read_heartbeat()
            and read_connect_heartbeat(1) == read_switch()
            and read_connect_heartbeat(3)[:3] == bytes([0xAA, 0x33, 0x00])
            and read_connect_heartbeat(4) == read_bar_switch()
        ),
    ),
    _check("read_heartbeat", lambda: bool(read_heartbeat() == bytes([0xAA] + [0] * 18 + [0xAA]))),
    _check("read_heartbeat", lambda: bool(read_heartbeat() == bytes([0xAA] + [0] * 18 + [0xAA]))),
    _check("read_heartbeat", lambda: bool(read_heartbeat() == bytes([0xAA] + [0] * 18 + [0xAA]))),
    _check("read_heartbeat", lambda: bool(read_heartbeat() == bytes([0xAA] + [0] * 18 + [0xAA]))),
    _check(
        "build_h5086_chart_time_range",
        lambda: bool(
            build_h5086_chart_time_range(0x01020304, 0x0A0B0C0D)[:11]
            == bytes([0x33, 0x01, 0x01, 0x02, 0x03, 0x04, 0x0A, 0x0B, 0x0C, 0x0D, 0x00])
            and build_h5086_chart_time_range(1700000000, 1700003600)[2:10]
            == (1700000000).to_bytes(4, "big") + (1700003600).to_bytes(4, "big")
        ),
    ),
    _check(
        "build_music_old_0x01",
        lambda: bool(
            build_music_old_0x01(1, 80, (1, 2, 3))[:9] == bytes([0x33, 0x05, 0x01, 0x01, 0x50, 1, 2, 3, 0])
            and build_music_old_0x01(0, 80)[:6] == bytes([0x33, 0x05, 0x01, 0x00, 0x50, 0x00])
            and build_music_old_0x01(2, 50)[5:8] == bytes([0xFF, 0x00, 0x00])
            and build_music_old_0x01(4, 50, (1, 2, 3))[5] == 0x00
        ),
    ),
    _check(
        "build_music_old_0x01",
        lambda: bool(
            build_music_old_0x01(1, 80, (1, 2, 3))[:9] == bytes([0x33, 0x05, 0x01, 0x01, 0x50, 1, 2, 3, 0])
            and build_music_old_0x01(0, 80)[:6] == bytes([0x33, 0x05, 0x01, 0x00, 0x50, 0x00])
            and build_music_old_0x01(2, 50)[5:8] == bytes([0xFF, 0x00, 0x00])
            and build_music_old_0x01(4, 50, (1, 2, 3))[5] == 0x00
        ),
    ),
    _check(
        "build_plug_relay",
        lambda: bool(
            [
                build_plug_relay(o, on)[2]
                for o, on in [
                    (0, True),
                    (0, False),
                    (1, True),
                    (1, False),
                    (2, True),
                    (2, False),
                    (15, True),
                    (15, False),
                ]
            ]
            == [0x11, 0x10, 0x22, 0x20, 0x44, 0x40, 0xFF, 0xF0]
            and build_plug_relay(0, True)[:2] == bytes([0x33, 0x01])
            and build_plug_relay(15, True)[19] == (0x33 ^ 0x01 ^ 0xFF)
        ),
    ),
    _check(
        "build_plug_relay",
        lambda: bool(
            [
                build_plug_relay(o, on)[2]
                for o, on in [
                    (0, True),
                    (0, False),
                    (1, True),
                    (1, False),
                    (2, True),
                    (2, False),
                    (15, True),
                    (15, False),
                ]
            ]
            == [0x11, 0x10, 0x22, 0x20, 0x44, 0x40, 0xFF, 0xF0]
            and build_plug_relay(0, True)[:2] == bytes([0x33, 0x01])
        ),
    ),
    _check(
        "build_plug_relay_with_secret",
        lambda: bool(
            [f[:3] for f in build_plug_relay_with_secret(15, True, bytes(range(8)))]
            == [bytes([0x33, 0xB2, 0x00]), bytes([0x33, 0x01, 0xFF])]
            and build_plug_relay_with_secret(15, False, bytes(8))[1][2] == 0xF0
            and build_plug_relay_with_secret(1, True, bytes(range(1, 9)))[0][2:10] == bytes(range(1, 9))
            and build_plug_relay_with_secret(1, True, bytes(8))[1][2] == 0x22
        ),
    ),
    _check(
        "build_h5086_chart_prepare",
        lambda: bool(build_h5086_chart_prepare() == bytes.fromhex("3302" + "00" * 17 + "31")),
    ),
    _check(
        "build_h5086_chart_prepare",
        lambda: bool(
            build_h5086_chart_prepare()[:3] == bytes([0x33, 0x02, 0x00])
            and build_h5086_chart_prepare()[19] == 0x33 ^ 0x02
        ),
    ),
    _check(
        "build_h5086_chart_prepare",
        lambda: bool(build_h5086_chart_prepare() == bytes.fromhex("3302" + "00" * 17 + "31")),
    ),
    _check(
        "build_h5089_total_switch",
        lambda: bool(
            build_h5089_total_switch(True)[:4] == bytes([0x33, 0x02, 0x01, 0x00])
            and build_h5089_total_switch(True)[19] == 0x33 ^ 0x02 ^ 0x01
            and build_h5089_total_switch(False)[19] == 0x33 ^ 0x02
        ),
    ),
    _check(
        "build_plug_total_switch",
        lambda: bool(
            build_plug_total_switch(True) == bytes([0x33, 0x02, 0x01] + [0] * 16 + [0x33 ^ 0x02 ^ 0x01])
            and build_plug_total_switch(False)[:3] == bytes([0x33, 0x02, 0x00])
        ),
    ),
    _check(
        "build_plug_total_switch",
        lambda: bool(
            build_plug_total_switch(True) == bytes([0x33, 0x02, 0x01] + [0] * 16 + [0x33 ^ 0x02 ^ 0x01])
            and build_plug_total_switch(False)[:3] == bytes([0x33, 0x02, 0x00])
        ),
    ),
    _check(
        "build_music_flag_sens_03",
        lambda: bool(
            build_music_flag_sens_03(50)[:5] == bytes([0x33, 0x05, 0x03, 0x00, 0x32])
            and build_music_flag_sens_03(99, (255, 0, 16))[:9]
            == bytes([0x33, 0x05, 0x03, 0x01, 0x63, 0xFF, 0x00, 0x10, 0x00])
        ),
    ),
    _check(
        "build_music_flag_sens_03",
        lambda: bool(
            build_music_flag_sens_03(50)[:5] == bytes([0x33, 0x05, 0x03, 0x00, 0x32])
            and build_music_flag_sens_03(99, (255, 0, 16))[:8]
            == bytes([0x33, 0x05, 0x03, 0x01, 0x63, 0xFF, 0x00, 0x10])
            and build_music_flag_sens_03(99, (0, 255, 0))[:9]
            == bytes([0x33, 0x05, 0x03, 0x01, 0x63, 0x00, 0xFF, 0x00, 0x00])
        ),
    ),
    _check(
        "build_music_flag_sens_03",
        lambda: bool(
            build_music_flag_sens_03(99) == bytes([0x33, 0x05, 0x03, 0x00, 0x63] + [0] * 14 + [0x56])
            and build_music_flag_sens_03(99, (0xFF, 0x00, 0x00))
            == bytes([0x33, 0x05, 0x03, 0x01, 0x63, 0xFF, 0x00, 0x00] + [0] * 11 + [0xA8])
        ),
    ),
    _check(
        "build_brightness_from_percent",
        lambda: bool(
            build_brightness_from_percent(1)[2] == 20
            and build_brightness_from_percent(100)[2] == 254
            and build_brightness_from_percent(50)[2] == 136
            and build_brightness_from_percent(50, 1, 100)[2] == 50
            and build_brightness_from_percent(0, 1, 100)[2] == 1
            and build_brightness_from_percent(100, 6, 254)[2] == 254
            and build_brightness_from_percent(100, 40, 254)[2] == 254
        ),
    ),
    _check(
        "read_brightness",
        lambda: bool(
            read_brightness()[:3] == bytes([0xAA, 0x04, 0x00])
            and read_brightness()[19] == (0xAA ^ 0x04)
            and len(read_brightness()) == 20
        ),
    ),
    _check(
        "build_color_0b_kelvin",
        lambda: bool(
            build_color_0b_kelvin(0x10, 0x20, 0x30, 2700, 0xFFFF)[:11]
            == bytes([0x33, 0x05, 0x0B, 0x10, 0x20, 0x30, 0x0A, 0x8C, 0xFF, 0x7F, 0x00])
        ),
    ),
    _check(
        "build_color_0b_white_tab",
        lambda: bool(
            build_color_0b_white_tab(0xFF, 0x8C, 0x3C, 15)[:9]
            == bytes([0x33, 0x05, 0x0B, 0xFF, 0x8C, 0x3C, 0xFF, 0x7F, 0x00])
            and build_color_0b_white_tab(1, 2, 3, 16)[6:8] == bytes([0xFF, 0xFF])
            and build_color_0b_white_tab(1, 2, 3, 20)[6:8] == bytes([0x00, 0x00])
        ),
    ),
    _check(
        "build_color_cct_0d",
        lambda: bool(
            build_color_cct_0d(0x10, 0x20, 0x30)[:11]
            == bytes([0x33, 0x05, 0x0D, 0x10, 0x20, 0x30, 0, 0, 0, 0, 0])
            and build_color_cct_0d(0xFF, 0x8C, 0x3C, kelvin=2700, tint=(0xFF, 0x8C, 0x3C))[:11]
            == bytes([0x33, 0x05, 0x0D, 0xFF, 0x8C, 0x3C, 0x0A, 0x8C, 0xFF, 0x8C, 0x3C])
            and build_color_cct_0d(0xFF, 0xFF, 0xFF, kelvin=6500, tint=(1, 2, 3))[:12]
            == bytes([0x33, 0x05, 0x0D, 0xFF, 0xFF, 0xFF, 0x19, 0x64, 1, 2, 3, 0])
        ),
    ),
    _check(
        "build_color_legacy",
        lambda: bool(
            build_color_legacy(0x11, 0x22, 0x33)[:10]
            == bytes([0x33, 0x05, 0x02, 0x11, 0x22, 0x33, 0, 0, 0, 0])
            and build_color_legacy(0xFF, 0xFF, 0xFF, 1, 0xFF, 0x8C, 0x3C)[:10]
            == bytes([0x33, 0x05, 0x02, 0xFF, 0xFF, 0xFF, 0x01, 0xFF, 0x8C, 0x3C])
            and build_color_legacy(1, 2, 3)[19] == (0x33 ^ 0x05 ^ 0x02 ^ 1 ^ 2 ^ 3)
        ),
    ),
    _check(
        "build_color_rgbic_0b",
        lambda: bool(
            build_color_rgbic_0b(0xFF, 0x00, 0x10, 0x7FFF)[:9]
            == bytes([0x33, 0x05, 0x0B, 0xFF, 0x00, 0x10, 0xFF, 0x7F, 0x00])
            and build_color_rgbic_0b(1, 2, 3, 0x0001)[6:9] == bytes([0x01, 0x00, 0x00])
            and build_color_rgbic_0b(1, 2, 3, 0x7FFFFF, mask_bytes=3)[6:10] == bytes([0xFF, 0xFF, 0x7F, 0x00])
        ),
    ),
    _check(
        "build_diy_activate",
        lambda: bool(
            build_diy_activate(0x1234)[:6] == bytes([0x33, 0x05, 0x0A, 0x34, 0x12, 0x00])
            and build_diy_activate(0x1234, 0x0102)[:7] == bytes([0x33, 0x05, 0x0A, 0x34, 0x12, 0x02, 0x01])
        ),
    ),
    _check(
        "build_diy_activate",
        lambda: bool(
            build_diy_activate(0x0102)[:8] == bytes([0x33, 0x05, 0x0A, 0x02, 0x01, 0x00, 0x00, 0x00])
            and build_diy_activate(0x0102, 0x0304)[:7] == bytes([0x33, 0x05, 0x0A, 0x02, 0x01, 0x04, 0x03])
        ),
    ),
    _check(
        "build_diy_activate_custom_music",
        lambda: bool(
            build_diy_activate_custom_music(0x1234, 0x01020304, 2)[:11]
            == bytes([0x33, 0x05, 0x0A, 0x34, 0x12, 0x04, 0x03, 0x02, 0x01, 0x02, 0x00])
        ),
    ),
    _check(
        "build_game_mode_h6057",
        lambda: bool(
            build_game_mode_h6057(0)[:5] == bytes([0x33, 0x05, 0x0B, 0x00, 0x00])
            and build_game_mode_h6057(1)[:5] == bytes([0x33, 0x05, 0x0B, 0x01, 0x00])
            and build_game_mode_h6057(3)[:4] == bytes([0x33, 0x05, 0x0B, 0x03])
            and build_game_mode_h6057(3)[4:19] == bytes(15)
            and build_game_mode_h6057(2)[19] == (0x33 ^ 0x05 ^ 0x0B ^ 0x02)
        ),
    ),
    _check(
        "build_mic_beat_color",
        lambda: bool(build_mic_beat_color(10, 20, 30)[:8] == bytes([0x33, 0x05, 0x05, 0x00, 10, 20, 30, 0])),
    ),
    _check(
        "build_mic_enter", lambda: bool(build_mic_enter()[:8] == bytes([0x33, 0x05, 0x05, 0x01, 0, 0, 0, 0]))
    ),
    _check(
        "build_mic_h6165", lambda: bool(build_mic_h6165()[:6] == bytes([0x33, 0x05, 0x05, 0x00, 0x01, 0x00]))
    ),
    _check("build_mic_h6192", lambda: bool(build_mic_h6192()[:4] == bytes([0x33, 0x05, 0x05, 0x00]))),
    _check(
        "build_mic_mode_color",
        lambda: bool(
            build_mic_mode_color(10, 20, 30)[:9] == bytes([0x33, 0x05, 0x05, 0x01, 0x01, 10, 20, 30, 0])
        ),
    ),
    _check(
        "build_mic_v4",
        lambda: bool(
            build_mic_v4(True)[:6] == bytes([0x33, 0x05, 0x05, 0x03, 0x01, 0x00])
            and build_mic_v4(False)[:5] == bytes([0x33, 0x05, 0x05, 0x03, 0x00])
        ),
    ),
    _check(
        "build_mic_via_music_0x13",
        lambda: bool(build_mic_via_music_0x13()[:5] == bytes([0x33, 0x05, 0x13, 0x10, 0x00])),
    ),
    _check(
        "build_music_code_only",
        lambda: bool(
            build_music_code_only(57)[:5] == bytes([0x33, 0x05, 0x13, 0x39, 0x00])
            and build_music_code_only(134)[:4] == bytes([0x33, 0x05, 0x13, 0x86])
            and build_music_code_only(57)[19] == (0x33 ^ 0x05 ^ 0x13 ^ 0x39)
        ),
    ),
    _check(
        "build_music_color",
        lambda: bool(
            build_music_color(7, 99, sub_mode=0x13)[:7] == bytes([0x33, 0x05, 0x13, 0x07, 0x63, 0x00, 0x00])
            and build_music_color(0, 80, (1, 2, 3), sub_mode=0x13)[:9]
            == bytes([0x33, 0x05, 0x13, 0x00, 0x50, 0x01, 1, 2, 3])
        ),
    ),
    _check(
        "build_music_color",
        lambda: bool(
            build_music_color(4, 60, (9, 8, 7), sub_mode=0x13)[:9]
            == bytes([0x33, 0x05, 0x13, 0x04, 0x3C, 0x01, 9, 8, 7])
            and build_music_color(6, 60, sub_mode=0x13)[:7]
            == bytes([0x33, 0x05, 0x13, 0x06, 0x3C, 0x00, 0x00])
        ),
    ),
    _check(
        "build_music_color",
        lambda: bool(
            build_music_color(3, 99, (255, 0, 0), sub_mode=0x0C)[:10]
            == bytes([0x33, 0x05, 0x0C, 0x03, 0x63, 0x01, 0xFF, 0x00, 0x00, 0x00])
            and build_music_color(2, 40, None, sub_mode=0x0C)[:7]
            == bytes([0x33, 0x05, 0x0C, 0x02, 0x28, 0x00, 0x00])
        ),
    ),
    _check(
        "build_music_color",
        lambda: bool(
            build_music_color(0, 99, sub_mode=0x0E)[:7] == bytes([0x33, 0x05, 0x0E, 0x00, 0x63, 0x00, 0x00])
            and build_music_color(3, 50, (1, 2, 3), sub_mode=0x0E)[:10]
            == bytes([0x33, 0x05, 0x0E, 0x03, 0x32, 0x01, 1, 2, 3, 0])
        ),
    ),
    _check(
        "build_music_color",
        lambda: bool(
            build_music_color(3, 60, (1, 2, 3), sub_mode=0x0F)[:10]
            == bytes([0x33, 0x05, 0x0F, 0x03, 0x3C, 0x01, 1, 2, 3, 0])
        ),
    ),
    _check(
        "build_music_color",
        lambda: bool(
            build_music_color(2, 90, (255, 0, 0), sub_mode=0x0C)[:9]
            == bytes([0x33, 0x05, 0x0C, 0x02, 0x5A, 0x01, 0xFF, 0x00, 0x00])
            and build_music_color(3, 50, None, sub_mode=0x0C)[:7]
            == bytes([0x33, 0x05, 0x0C, 0x03, 0x32, 0x00, 0x00])
        ),
    ),
    _check(
        "build_music_color",
        lambda: bool(
            build_music_color(16, 70, (0, 255, 0), sub_mode=0x11)[:9]
            == bytes([0x33, 0x05, 0x11, 0x10, 0x46, 0x01, 0x00, 0xFF, 0x00])
            and build_music_color(18, 70, sub_mode=0x11)[:7]
            == bytes([0x33, 0x05, 0x11, 0x12, 0x46, 0x00, 0x00])
        ),
    ),
    _check(
        "build_music_color",
        lambda: bool(
            build_music_color(6, 60, (1, 2, 3), sub_mode=0x0F)[:9]
            == bytes([0x33, 0x05, 0x0F, 0x06, 0x3C, 0x01, 1, 2, 3])
            and build_music_color(0, 60, sub_mode=0x0F)[:6] == bytes([0x33, 0x05, 0x0F, 0x00, 0x3C, 0x00])
        ),
    ),
    _check(
        "build_music_color",
        lambda: bool(
            build_music_color(0, 80, None, sub_mode=0x0E)[:6] == bytes([0x33, 0x05, 0x0E, 0x00, 0x50, 0x00])
            and build_music_color(1, 80, (255, 0, 0), sub_mode=0x0E)[:9]
            == bytes([0x33, 0x05, 0x0E, 0x01, 0x50, 0x01, 0xFF, 0x00, 0x00])
        ),
    ),
    _check(
        "build_music_color",
        lambda: bool(
            build_music_color(2, 40, (9, 8, 7), sub_mode=0x0C)[:10]
            == bytes([0x33, 0x05, 0x0C, 0x02, 0x28, 0x01, 9, 8, 7, 0])
        ),
    ),
    _check(
        "build_music_color",
        lambda: bool(
            build_music_color(16, 50, (1, 2, 3), sub_mode=0x11)[:10]
            == bytes([0x33, 0x05, 0x11, 0x10, 0x32, 0x01, 1, 2, 3, 0])
            and build_music_color(18, 50, sub_mode=0x11)[:7]
            == bytes([0x33, 0x05, 0x11, 0x12, 0x32, 0x00, 0x00])
        ),
    ),
    _check(
        "build_music_dyn_before_sens",
        lambda: bool(
            build_music_dyn_before_sens(75, 80, 1)[:7] == bytes([0x33, 0x05, 0x13, 0x4B, 0x01, 0x50, 0x00])
            and build_music_dyn_before_sens(10, 80, 0)[:6] == bytes([0x33, 0x05, 0x13, 0x0A, 0x00, 0x50])
            and build_music_dyn_before_sens(75, 99, 1, 2)[:7]
            == bytes([0x33, 0x05, 0x13, 0x4B, 0x01, 0x02, 0x63])
        ),
    ),
    _check(
        "build_music_dyn_color",
        lambda: bool(
            build_music_dyn_color(4, 99, 0, (255, 0, 0))[:10]
            == bytes([0x33, 0x05, 0x13, 0x04, 0x63, 0x00, 0x01, 0xFF, 0x00, 0x00])
            and build_music_dyn_color(4, 99, 1)[:8] == bytes([0x33, 0x05, 0x13, 0x04, 0x63, 0x01, 0x00, 0x00])
            and build_music_dyn_color(4, 70, 0)[:8] == bytes([0x33, 0x05, 0x13, 0x04, 0x46, 0x00, 0x00, 0x00])
        ),
    ),
    _check(
        "build_music_dyn_color",
        lambda: bool(
            build_music_dyn_color(5, 50, 1)[:8] == bytes([0x33, 0x05, 0x13, 0x05, 0x32, 0x01, 0x00, 0x00])
            and build_music_dyn_color(3, 50, 0, (0, 0, 255))[:10]
            == bytes([0x33, 0x05, 0x13, 0x03, 0x32, 0x00, 0x01, 0x00, 0x00, 0xFF])
            and build_music_dyn_color(6, 60, 0, (9, 8, 7))[:10]
            == bytes([0x33, 0x05, 0x13, 0x06, 0x3C, 0x00, 0x01, 9, 8, 7])
        ),
    ),
    _check(
        "build_music_dyn_color",
        lambda: bool(
            build_music_dyn_color(4, 80, 0, (255, 0, 0))[:10]
            == bytes([0x33, 0x05, 0x13, 0x04, 0x50, 0x00, 0x01, 0xFF, 0x00, 0x00])
            and build_music_dyn_color(6, 50, 0)[:8] == bytes([0x33, 0x05, 0x13, 0x06, 0x32, 0x00, 0x00, 0x00])
            and build_music_dyn_color(3, 99, 1, (0x12, 0x34, 0x56))[:10]
            == bytes([0x33, 0x05, 0x13, 0x03, 0x63, 0x01, 0x01, 0x12, 0x34, 0x56])
            and build_music_dyn_color(3, 99, 1, (0x12, 0x34, 0x56))[10:19] == bytes(9)
        ),
    ),
    _check(
        "build_music_dyn_color",
        lambda: bool(
            build_music_dyn_color(3, 80, 0, (1, 2, 3))[:10]
            == bytes([0x33, 0x05, 0x13, 0x03, 0x50, 0x00, 0x01, 0x01, 0x02, 0x03])
            and build_music_dyn_color(6, 80, 0, (255, 0, 0))[:10]
            == bytes([0x33, 0x05, 0x13, 0x06, 0x50, 0x00, 0x01, 0xFF, 0x00, 0x00])
            and build_music_dyn_color(4, 80, 0)[:8] == bytes([0x33, 0x05, 0x13, 0x04, 0x50, 0x00, 0x00, 0x00])
            and build_music_dyn_color(5, 99, 0) == build_music_code_sens(5, 99)
        ),
    ),
    _check(
        "build_music_dyn_color",
        lambda: bool(
            build_music_dyn_color(3, 99, 0, (255, 0, 0))[:10]
            == bytes([0x33, 0x05, 0x13, 0x03, 0x63, 0x00, 0x01, 0xFF, 0x00, 0x00])
            and build_music_dyn_color(5, 99, 1)[:8] == bytes([0x33, 0x05, 0x13, 0x05, 0x63, 0x01, 0x00, 0x00])
        ),
    ),
    _check(
        "build_music_dyn_color",
        lambda: bool(
            build_music_dyn_color(4, 50, 0, (0, 0, 255))[:10]
            == bytes([0x33, 0x05, 0x13, 0x04, 0x32, 0x00, 0x01, 0x00, 0x00, 0xFF])
            and build_music_dyn_color(3, 50, 1, (9, 8, 7))[:10]
            == bytes([0x33, 0x05, 0x13, 0x03, 0x32, 0x01, 0x01, 0x09, 0x08, 0x07])
            and build_music_dyn_color(4, 50, 0) != build_music_dyn_color(4, 50, 0, (0, 0, 255))
            and build_music_dyn_color(3, 99, 0, (255, 0, 0))[:10]
            == bytes([0x33, 0x05, 0x13, 0x03, 0x63, 0x00, 0x01, 0xFF, 0x00, 0x00])
        ),
    ),
    _check(
        "build_music_dyn_color",
        lambda: bool(
            build_music_dyn_color(5, 99, 0, (255, 0, 0), sub_mode=0x0C)[:10]
            == bytes([0x33, 0x05, 0x0C, 0x05, 0x63, 0x00, 0x01, 0xFF, 0x00, 0x00])
            and build_music_dyn_color(5, 99, 1, None, sub_mode=0x0C)[:8]
            == bytes([0x33, 0x05, 0x0C, 0x05, 0x63, 0x01, 0x00, 0x00])
        ),
    ),
    _check(
        "build_music_dyn_color",
        lambda: bool(
            build_music_dyn_color(4, 60, 0, (0, 255, 0), sub_mode=0x0F)[:11]
            == bytes([0x33, 0x05, 0x0F, 0x04, 0x3C, 0x00, 0x01, 0x00, 0xFF, 0x00, 0x00])
        ),
    ),
    _check(
        "build_music_dyn_color",
        lambda: bool(
            build_music_dyn_color(4, 80, 0, (255, 0, 0))[:10]
            == bytes([0x33, 0x05, 0x13, 0x04, 0x50, 0x00, 0x01, 0xFF, 0x00, 0x00])
            and build_music_dyn_color(3, 99, 1, (0, 0, 255))[:10]
            == bytes([0x33, 0x05, 0x13, 0x03, 0x63, 0x01, 0x01, 0x00, 0x00, 0xFF])
            and build_music_dyn_color(5, 60, 2, (1, 2, 3))[:10]
            == bytes([0x33, 0x05, 0x13, 0x05, 0x3C, 0x02, 0x01, 0x01, 0x02, 0x03])
            and build_music_dyn_color(6, 70, 0)[:8] == bytes([0x33, 0x05, 0x13, 0x06, 0x46, 0x00, 0x00, 0x00])
        ),
    ),
    _check(
        "build_music_dyn_color",
        lambda: bool(
            build_music_dyn_color(5, 99, 1)[:11]
            == bytes([0x33, 0x05, 0x13, 0x05, 0x63, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00])
            and build_music_dyn_color(3, 70, 0, (1, 2, 3))[:10]
            == bytes([0x33, 0x05, 0x13, 0x03, 0x46, 0x00, 0x01, 1, 2, 3])
            and build_music_dyn_color(2, 80, 0, (255, 0, 0))[:10]
            == bytes([0x33, 0x05, 0x13, 0x02, 0x50, 0x00, 0x01, 0xFF, 0x00, 0x00])
        ),
    ),
    _check(
        "build_music_dyn_color",
        lambda: bool(
            build_music_dyn_color(5, 50, 1, (1, 2, 3), sub_mode=0x0C)[:10]
            == bytes([0x33, 0x05, 0x0C, 0x05, 0x32, 0x01, 0x01, 1, 2, 3])
            and build_music_dyn_color(5, 50, 0, sub_mode=0x0C)[:8]
            == bytes([0x33, 0x05, 0x0C, 0x05, 0x32, 0x00, 0x00, 0x00])
        ),
    ),
    _check(
        "build_music_dyn_color",
        lambda: bool(
            build_music_dyn_color(17, 70, 1, (1, 2, 3), sub_mode=0x11)[:10]
            == bytes([0x33, 0x05, 0x11, 0x11, 0x46, 0x01, 0x01, 1, 2, 3])
            and build_music_dyn_color(17, 70, 0, sub_mode=0x11)[:8]
            == bytes([0x33, 0x05, 0x11, 0x11, 0x46, 0x00, 0x00, 0x00])
        ),
    ),
    _check(
        "build_music_dyn_color",
        lambda: bool(
            build_music_dyn_color(4, 70, 1, (255, 0, 0), sub_mode=0x0F)[:10]
            == bytes([0x33, 0x05, 0x0F, 0x04, 0x46, 0x01, 0x01, 0xFF, 0x00, 0x00])
            and build_music_dyn_color(4, 60, 0, sub_mode=0x0F)[:8]
            == bytes([0x33, 0x05, 0x0F, 0x04, 0x3C, 0x00, 0x00, 0x00])
        ),
    ),
    _check(
        "build_music_dyn_color",
        lambda: bool(
            build_music_dyn_color(5, 40, 1, sub_mode=0x0C)[:7]
            == bytes([0x33, 0x05, 0x0C, 0x05, 0x28, 0x01, 0x00])
            and build_music_dyn_color(1, 40, 3, None, sub_mode=0x0C)[:7]
            == bytes([0x33, 0x05, 0x0C, 0x01, 0x28, 0x03, 0x00])
        ),
    ),
    _check(
        "build_music_dyn_color",
        lambda: bool(
            build_music_dyn_color(17, 50, 1, sub_mode=0x11)[:8]
            == bytes([0x33, 0x05, 0x11, 0x11, 0x32, 0x01, 0x00, 0x00])
        ),
    ),
    _check(
        "build_music_dyn_color",
        lambda: bool(
            build_music_dyn_color(5, 80, 0, (255, 0, 0))[:11]
            == bytes([0x33, 0x05, 0x13, 0x05, 0x50, 0x00, 0x01, 0xFF, 0x00, 0x00, 0x00])
            and build_music_dyn_color(3, 80, 1)[:8] == bytes([0x33, 0x05, 0x13, 0x03, 0x50, 0x01, 0x00, 0x00])
            and build_music_dyn_color(6, 99, 0, (0, 0, 255))[:10]
            == bytes([0x33, 0x05, 0x13, 0x06, 0x63, 0x00, 0x01, 0x00, 0x00, 0xFF])
        ),
    ),
    _check(
        "build_music_h6047",
        lambda: bool(
            build_music_h6047(50, 75, 1, 0, headphone=True)[:9]
            == bytes([0x33, 0x05, 0x13, 0x32, 0x4B, 0x01, 0x00, 0x01, 0x00])
            and build_music_h6047(50, 101, 7)[:8] == bytes([0x33, 0x05, 0x13, 0x32, 0x65, 0x00, 0x07, 0x00])
        ),
    ),
    _check(
        "build_music_h6127_newdetail",
        lambda: bool(
            build_music_h6127_newdetail(4, 80, (1, 2, 3))[:9]
            == bytes([0x33, 0x05, 0x01, 0x04, 0x50, 1, 2, 3, 0])
            and build_music_h6127_newdetail(3, 80, (1, 2, 3))[:6]
            == bytes([0x33, 0x05, 0x01, 0x03, 0x50, 0x00])
            and build_music_h6127_newdetail(0, 80)[:6] == bytes([0x33, 0x05, 0x01, 0x00, 0x50, 0x00])
        ),
    ),
    _check(
        "build_music_h6148",
        lambda: bool(
            build_music_h6148(1, 99, 0, 0)[:11]
            == bytes([0x33, 0x05, 0x0F, 0x01, 0x00, 0x63, 0x00, 0xFF, 0x00, 0x00, 0x00])
            and build_music_h6148(0, 50, 1, 1, (0x10, 0x20, 0x30))[:10]
            == bytes([0x33, 0x05, 0x0F, 0x00, 0x01, 0x32, 0x01, 0x10, 0x20, 0x30])
            and build_music_h6148(-1, 50, 0, 1)[3] == 0x00
            and build_music_h6148(5, 50, 0, 1)[3] == 0x01
            and build_music_h6148(0, 40, 0, 0, (0, 0, 0))[:11]
            == bytes([0x33, 0x05, 0x0F, 0x00, 0x00, 0x28, 0x00, 0x00, 0x00, 0x00, 0x00])
        ),
    ),
    _check(
        "build_music_h61a8",
        lambda: bool(
            build_music_h61a8(5, 80, (1, 0xFF0000)) == bytes.fromhex("33051305500001ff00" + "00" * 10 + "8e")
            and build_music_h61a8(4, 80, (0,))[:8] == bytes([0x33, 0x05, 0x13, 0x04, 0x50, 0x00, 0x00, 0x00])
            and build_music_h61a8(3, 80, (1, 1, 0x00FF00))[:11]
            == bytes([0x33, 0x05, 0x13, 0x03, 0x50, 0x01, 0x01, 0x00, 0xFF, 0x00, 0x00])
            and build_music_h61a8(3, 80, (0, 0, -65536))[:8]
            == bytes([0x33, 0x05, 0x13, 0x03, 0x50, 0x00, 0x00, 0x00])
            and build_music_h61a8(6, 50)[2] == 0x13
        ),
    ),
    _check(
        "build_music_h61a8",
        lambda: bool(
            build_music_h61a8(5, 80, (1, 0xFF0000))[:10]
            == bytes([0x33, 0x05, 0x13, 0x05, 0x50, 0x00, 0x01, 0xFF, 0x00, 0x00])
            and build_music_h61a8(4, 80, (0,))[:8] == bytes([0x33, 0x05, 0x13, 0x04, 0x50, 0x00, 0x00, 0x00])
            and build_music_h61a8(3, 80, (1, 1, 0x00FF00))[:11]
            == bytes([0x33, 0x05, 0x13, 0x03, 0x50, 0x01, 0x01, 0x00, 0xFF, 0x00, 0x00])
        ),
    ),
    _check(
        "build_op15_brightness",
        lambda: bool(
            build_op15_brightness(50, 0x1FFF)[:8] == bytes([0x33, 0x05, 0x15, 0x02, 50, 0xFF, 0x1F, 0x00])
            and build_op15_brightness(100, 0x7FFFFF, mask_bytes=3)[5:9] == bytes([0xFF, 0xFF, 0x7F, 0x00])
        ),
    ),
    _check(
        "build_op15_brightness_group",
        lambda: bool(
            build_op15_brightness_group(1, list(range(10, 24)))[:19]
            == bytes([0x33, 0x05, 0x15, 0x03, 0x01, *range(10, 24)])
            and build_op15_brightness_group(2, [50, 60])[:8]
            == bytes([0x33, 0x05, 0x15, 0x03, 0x02, 50, 60, 0])
        ),
    ),
    _check(
        "build_op15_brightness_group",
        lambda: bool(
            build_op15_brightness_group(1, [50] * 14)[:19]
            == bytes([0x33, 0x05, 0x15, 0x03, 0x01] + [50] * 14)
            and build_op15_brightness_group(2, [10, 20])[:8]
            == bytes([0x33, 0x05, 0x15, 0x03, 0x02, 10, 20, 0])
        ),
    ),
    _check(
        "build_op15_brightness_set",
        lambda: bool(
            build_op15_brightness_set([100] * 15)[:19] == bytes([0x33, 0x05, 0x15, 0x03] + [100] * 15)
        ),
    ),
    _check(
        "build_op15_brightness_set",
        lambda: bool(
            build_op15_brightness_set([100] * 10)[:16]
            == bytes([0x33, 0x05, 0x15, 0x03] + [100] * 10 + [0, 0])
            and build_op15_brightness_set(list(range(1, 13)))[:16]
            == bytes([51, 5, 21, 3, *list(range(1, 13))])
            and build_op15_brightness_set([100] * 15)[:19] == bytes([0x33, 0x05, 0x15, 0x03] + [100] * 15)
        ),
    ),
    _check(
        "build_op15_color_group",
        lambda: bool(
            build_op15_color_group(2, [(255, 0, 0), (0, 255, 0), (0, 0, 255), (1, 2, 3)])[:18]
            == bytes([0x33, 0x05, 0x15, 0x04, 0x02, 255, 0, 0, 0, 255, 0, 0, 0, 255, 1, 2, 3, 0])
        ),
    ),
    _check(
        "build_op15_color_group",
        lambda: bool(
            build_op15_color_group(1, [(255, 0, 0), (0, 255, 0), (0, 0, 255), (1, 2, 3)])[:17]
            == bytes([0x33, 0x05, 0x15, 0x04, 0x01, 255, 0, 0, 0, 255, 0, 0, 0, 255, 1, 2, 3])
        ),
    ),
    _check(
        "build_op15_color_temp_04",
        lambda: bool(
            build_op15_color_temp_04(2700)[:7] == bytes([0x33, 0x05, 0x15, 0x04, 0x0A, 0x8C, 0x00])
            and build_op15_color_temp_04(6500)[4:6] == bytes([0x19, 0x64])
        ),
    ),
    _check(
        "build_op15_color_temp_04",
        lambda: bool(build_op15_color_temp_04(2700)[:7] == bytes([0x33, 0x05, 0x15, 0x04, 0x0A, 0x8C, 0x00])),
    ),
    _check(
        "build_op15_color_temp_04",
        lambda: bool(build_op15_color_temp_04(6500)[:7] == bytes([0x33, 0x05, 0x15, 0x04, 0x19, 0x64, 0x00])),
    ),
    _check(
        "build_op15_color_v5",
        lambda: bool(
            build_op15_color_v5(0xFF, 0x00, 0x00, 0x7FFFFF, mask_bytes=3)[:13]
            == bytes([0x33, 0x05, 0x15, 0x01, 0xFF, 0x00, 0x00, 0x00, 0x00, 0xFF, 0xFF, 0x7F, 0x00])
            and build_op15_color_v5(0xFF, 0x8C, 0x3C, 0x0003, kelvin=2700)[:11]
            == bytes([0x33, 0x05, 0x15, 0x01, 0xFF, 0x8C, 0x3C, 0x0A, 0x8C, 0x03, 0x00])
            and build_op15_color_v5(0x10, 0x20, 0x30, 0x3F)
            == append_bcc(bytes([0x33, 0x05, 0x15, 0x01, 0x10, 0x20, 0x30, 0x00, 0x00, 0x3F, 0x00]))
            and build_op15_color_v5(0xFF, 0x8C, 0x3C, 0x7FFFFF, kelvin=2700, mask_bytes=3)
            == append_bcc(bytes([0x33, 0x05, 0x15, 0x01, 0xFF, 0x8C, 0x3C, 0x0A, 0x8C, 0xFF, 0xFF, 0x7F]))
        ),
    ),
    _check(
        "build_op15_gradient_color",
        lambda: bool(
            build_op15_gradient_color(
                [(1, 2, 3), (4, 5, 6), (7, 8, 9)], [True, False, True] + [False] * 6 + [True]
            )[:16]
            == bytes([0x33, 0x05, 0x15, 0x05, 1, 2, 3, 4, 5, 6, 7, 8, 9, 0x05, 0x02, 0x00])
            and build_op15_gradient_color([(0, 0, 0)] * 3, [True] * 20)[13:17]
            == bytes([0xFF, 0xFF, 0x0F, 0x00])
            and build_op15_gradient_color([(1, 2, 3), (4, 5, 6), (7, 8, 9)], [True])[19]
            == (0x33 ^ 0x05 ^ 0x15 ^ 0x05 ^ 1 ^ 2 ^ 3 ^ 4 ^ 5 ^ 6 ^ 7 ^ 8 ^ 9 ^ 0x01)
        ),
    ),
    _check(
        "build_op15_gradient_color_wide",
        lambda: bool(
            build_op15_gradient_color([(1, 2, 3), (4, 5, 6), (7, 8, 9)], [True] * 33).hex()
            == "33051505010203040506070809ffffffff010026"
            and build_op15_gradient_color([(1, 2, 3), (4, 5, 6), (7, 8, 9)], [True] * 48).hex()
            == "33051505010203040506070809ffffffffffff27"
            and _raises(ValueError, lambda: build_op15_gradient_color([(0, 0, 0)] * 3, [True] * 49))
        ),
    ),
    _check(
        "build_op15_color_bytes_h",
        lambda: bool(
            build_op15_color_bytes_h(1, 2, 3, [True] * 13).hex() == "330515010102030000000000ff1f0000000000c2"
            and build_op15_color_bytes_h(0xFF, 0, 0, [True] * 33).hex()  # H6062 (4,1): 33 segments
            == "33051501ff00000000000000ffffffff010000dc"
            and build_op15_color_bytes_h(0, 0, 0xFF, [True] + [False] * 31 + [True]).hex()
            == "330515010000ff000000000001000000010000dd"
            and build_op15_color_bytes_h(0x10, 0x20, 0x30, [True] * 57)
            == build_op15_color_bytes_h(0x10, 0x20, 0x30, [True] * 56)  # coerceAtMost: 7 mask bytes
        ),
    ),
    _check(
        "build_op15_color_basic",
        lambda: bool(
            build_op15_color_basic(1, 2, 3, [True] * 14).hex() == "33051501010203ff3f00000000000000000000e2"
            and build_op15_color_basic(0xFF, 0, 0, [True] * 33).hex()  # BleUtils.X above 16 segments
            == "33051501ff0000ffffffff0100000000000000dc"
            and len(build_op15_color_basic(0, 0, 0, [True] * 96)) == 20
            and _raises(ValueError, lambda: build_op15_color_basic(0, 0, 0, [True] * 97))
        ),
    ),
    _check(
        "build_op15_color_temp_bytes_r",
        lambda: bool(
            build_op15_color_temp_bytes_r((255, 213, 161), 4000, [True] * 33).hex()
            == "33051501ffffff0fa0ffd5a1ffffffff010000f8"
            and build_op15_color_temp_bytes_r((255, 174, 84), 2700, [True] * 16).hex()
            == "33051501ffffff0a8cffae54ffff00000000005e"
            and build_op15_color_temp_bytes_r((255, 174, 84), 2700, [True] * 3, 0).hex()
            == "330515010000000a8cffae5407000000000000a6"
            and build_op15_color_temp_bytes_r((255, 174, 84), 2700, [True] * 3, 3)
            == build_op15_color_temp_bytes_r((255, 174, 84), 2700, [True] * 3, 0)
            and build_op15_color_temp_bytes_r((255, 174, 84), 2700, [True, False, True], 3).hex()
            == "33051501ffae5400000000000500000000000022"
        ),
    ),
    _check(
        "build_op15_kelvin_05",
        lambda: bool(
            build_op15_kelvin_05(2700)[:7] == bytes([0x33, 0x05, 0x15, 0x05, 0x0A, 0x8C, 0x00])
            and build_op15_kelvin_05(6500)[:7] == bytes([0x33, 0x05, 0x15, 0x05, 0x19, 0x64, 0x00])
            and build_op15_kelvin_05(6500)[19] == (0x33 ^ 0x05 ^ 0x15 ^ 0x05 ^ 0x19 ^ 0x64)
        ),
    ),
    _check(
        "build_op15_relative_brightness",
        lambda: bool(
            build_op15_relative_brightness(50)[:7] == bytes([0x33, 0x05, 0x15, 0x05, 0x03, 50, 0x00])
            and build_op15_relative_brightness(50, [False, True])[4] == 2
            and build_op15_relative_brightness(50, [True, False])[4] == 1
            and build_op15_relative_brightness(50, [False, False])[4] == 2
            and build_op15_relative_brightness(50, [True])[4] == 3
            and build_op15_relative_brightness(50, [True, True, False])[4] == 3
        ),
    ),
    _check(
        "build_op15_set_mode",
        lambda: bool(build_op15_set_mode()[:5] == bytes([0x33, 0x05, 0x15, 0x00, 0x00])),
    ),
    _check(
        "build_op15_side_brightness",
        lambda: bool(
            build_op15_side_brightness(50)[:7] == bytes([0x33, 0x05, 0x15, 0x05, 0x03, 50, 0x00])
            and build_op15_side_brightness(50, True, False)[4] == 1
            and build_op15_side_brightness(50, False, True)[4] == 2
            and build_op15_side_brightness(50, False, False)[4] == 2
        ),
    ),
    _check(
        "build_operate_mode_20",
        lambda: bool(build_operate_mode_20() == bytes([0x33, 0x05, 0x20] + [0] * 16 + [0x33 ^ 0x05 ^ 0x20])),
    ),
    _check(
        "build_scene_ext_carousel",
        lambda: bool(
            build_scene_ext_carousel(0x0203, 0x0A0B0C0D, 2)[:11]
            == bytes([0x33, 0x05, 0x04, 0x03, 0x02, 0x0D, 0x0C, 0x0B, 0x0A, 0x02, 0x00])
            and build_scene_ext_carousel(0x0203) == build_scene(0x0203)
        ),
    ),
    _check(
        "build_scene_newdetail",
        lambda: bool(
            build_scene_newdetail(0x0203)
            == bytes([0x33, 0x05, 0x04, 0x03, 0x02] + [0] * 14 + [0x33 ^ 0x05 ^ 0x04 ^ 0x03 ^ 0x02])
            and build_scene_newdetail(0x0203, 0x0506)[:8]
            == bytes([0x33, 0x05, 0x04, 0x03, 0x02, 0x06, 0x05, 0x00])
            and build_scene_newdetail(0x0203) == build_scene(0x0203)
        ),
    ),
    _check(
        "build_video_h6101",
        lambda: bool(build_video_h6101(True, False)[:6] == bytes([0x33, 0x05, 0x00, 0x02, 0x01, 0x00])),
    ),
    _check(
        "build_video_h6104",
        lambda: bool(build_video_h6104(True, 7)[:7] == bytes([0x33, 0x05, 0x00, 0x00, 0x00, 0x07, 0x00])),
    ),
    _check(
        "build_diy_old_07",
        lambda: bool(
            build_diy_old_07(3, 1, 2, 50, [(255, 0, 0), (0, 0, 255)])[:13]
            == bytes([0x33, 0x05, 0x07, 0x03, 0x01, 0x02, 0x32, 0xFF, 0x00, 0x00, 0x00, 0x00, 0xFF])
            and build_diy_old_07(0, 0, 0, 0) == bytes([0x33, 0x05, 0x07] + [0] * 16 + [0x31])
        ),
    ),
    _check(
        "build_diy_old_07",
        lambda: bool(
            build_diy_old_07(3, 1, 2, 50, [(255, 0, 0), (0, 0, 255)])[:13]
            == bytes([0x33, 0x05, 0x07, 0x03, 0x01, 0x02, 0x32, 0xFF, 0x00, 0x00, 0x00, 0x00, 0xFF])
            and build_diy_old_07(3, 1, 2, 50)[7:19] == bytes(12)
            and build_diy_old_07(0, 0, 0, 0) == bytes([0x33, 0x05, 0x07] + [0] * 16 + [0x31])
        ),
    ),
    _check(
        "read_device_other_info",
        lambda: bool(read_device_other_info() == bytes([0xAA, 0x07, 0x08] + [0] * 16 + [0xA5])),
    ),
    _check(
        "read_device_other_info",
        lambda: bool(read_device_other_info() == bytes([0xAA, 0x07, 0x08] + [0] * 16 + [0xA5])),
    ),
    _check(
        "read_device_other_info",
        lambda: bool(read_device_other_info() == bytes([0xAA, 0x07, 0x08] + [0] * 16 + [0xA5])),
    ),
    _check("read_device_sn", lambda: bool(read_device_sn() == bytes([0xAA, 0x07, 0x02] + [0] * 16 + [0xAF]))),
    _check(
        "read_dsp_version", lambda: bool(read_dsp_version() == bytes([0xAA, 0x07, 0x07] + [0] * 16 + [0xAA]))
    ),
    _check(
        "read_hard_version",
        lambda: bool(read_hard_version() == bytes([0xAA, 0x07, 0x03] + [0] * 16 + [0xAE])),
    ),
    _check(
        "read_mcu_hard_version",
        lambda: bool(read_mcu_hard_version() == bytes([0xAA, 0x07, 0x0B] + [0] * 16 + [0xA6])),
    ),
    _check(
        "read_mcu_soft_version",
        lambda: bool(read_mcu_soft_version() == bytes([0xAA, 0x07, 0x0A] + [0] * 16 + [0xA7])),
    ),
    _check(
        "read_soft_version_in_device_info",
        lambda: bool(read_soft_version_in_device_info() == bytes([0xAA, 0x07, 0x04] + [0] * 16 + [0xA9])),
    ),
    _check(
        "build_h6101_calibration",
        lambda: bool(build_h6101_calibration() == bytes([0x33, 0x08] + [0] * 17 + [0x33 ^ 0x08])),
    ),
    _check(
        "build_h6101_calibration",
        lambda: bool(build_h6101_calibration() == bytes.fromhex("3308" + "00" * 17 + "3b")),
    ),
    _check(
        "build_h6101_calibration",
        lambda: bool(build_h6101_calibration() == bytes([0x33, 0x08] + [0] * 17 + [0x33 ^ 0x08])),
    ),
    _check(
        "build_string_scene_09",
        lambda: bool(
            build_string_scene_09(4)[:5] == bytes([0x33, 0x05, 0x09, 0x05, 0x00])
            and build_string_scene_09(4)[4:19] == bytes(15)
            and build_string_scene_09(0)[:4] == bytes([0x33, 0x05, 0x09, 0x01])
        ),
    ),
    _check(
        "build_string_scene_09",
        lambda: bool(
            build_string_scene_09(4)[:5] == bytes([0x33, 0x05, 0x09, 0x05, 0x00])
            and build_string_scene_09(4)[4:19] == bytes(15)
            and build_string_scene_09(0)[:4] == bytes([0x33, 0x05, 0x09, 0x01])
        ),
    ),
    _check(
        "build_sync_time_stamp",
        lambda: bool(
            build_sync_time_stamp(0x01020304, 5, 30)[:10]
            == bytes([0x33, 0x09, 0x01, 0x02, 0x03, 0x04, 0x01, 0x05, 0x1E, 0x00])
            and build_sync_time_stamp(0x01020304, 5, 30)[2:9] == build_plug_sync_time(0x01020304, 5, 30)[2:9]
        ),
    ),
    _check(
        "build_sync_time_v0",
        lambda: bool(
            build_sync_time_v0(13, 5, 9, 7, -5, 0, 15, 10, 2026, 0x01020304)[:18]
            == bytes(
                [
                    0x33,
                    0x09,
                    0x0D,
                    0x05,
                    0x09,
                    0x07,
                    0x01,
                    0xFB,
                    0x00,
                    0x0F,
                    0x0A,
                    0xEA,
                    0x07,
                    0x04,
                    0x03,
                    0x02,
                    0x01,
                    0x00,
                ]
            )
            and build_sync_time_v0(13, 5, 9, 7, -5, 0, 15, 10, 2026, 0x01020304)[:9]
            == build_sync_time(13, 5, 9, 7, -5, 0)[:9]
        ),
    ),
    _check(
        "read_daysync_device_info",
        lambda: bool(
            read_daysync_device_info()[:3] == bytes([0xAC, 0x09, 0x00])
            and read_daysync_device_info()[19] == 0xAC ^ 0x09
        ),
    ),
    _check(
        "build_auto_time",
        lambda: bool(
            build_auto_time(0, True, 7, 30, 22, 0, 0x00)[:10]
            == bytes([0x33, 0x0A, 0x01, 0x07, 0x1E, 0x16, 0x00, 0x00, 0x00, 0x00])
            and build_auto_time(1, False, 6, 0, 23, 59, 0x80)[2:9]
            == bytes([0x00, 0x06, 0x00, 0x17, 0x3B, 0x01, 0x80])
        ),
    ),
    _check(
        "build_auto_time",
        lambda: bool(
            build_auto_time(0, True, 7, 30, 22, 0, 0x00)[:10]
            == bytes([0x33, 0x0A, 0x01, 7, 30, 22, 0, 0x00, 0x00, 0x00])
            and build_auto_time(3, False, 23, 59, 6, 15, 0x80)[2:9]
            == bytes([0x00, 23, 59, 6, 15, 0x03, 0x80])
        ),
    ),
    _check(
        "read_auto_time", lambda: bool(read_auto_time(1) == bytes([0xAA, 0x0A, 0x01] + [0] * 16 + [0xA1]))
    ),
    _check(
        "read_auto_time",
        lambda: bool(
            read_auto_time(0)[:4] == bytes([0xAA, 0x0A, 0x00, 0x00])
            and read_auto_time(1) == bytes([0xAA, 0x0A, 0x01] + [0] * 16 + [0xA1])
        ),
    ),
    _check(
        "build_delay_close",
        lambda: bool(
            build_delay_close(True, 90)[:6] == bytes([0x33, 0x0B, 0x01, 0x01, 0x1E, 0x00])
            and build_delay_close(False, -5)[2:5] == bytes([0x00, 0x00, 0x00])
        ),
    ),
    _check(
        "build_game_h6057",
        lambda: bool(
            build_game_h6057(3) == bytes([0x33, 0x05, 0x0B, 0x03] + [0] * 15 + [0x33 ^ 0x05 ^ 0x0B ^ 0x03])
        ),
    ),
    _check("read_delay_close", lambda: bool(read_delay_close() == bytes([0xAA, 0x0B] + [0] * 17 + [0xA1]))),
    _check(
        "read_mode_bare", lambda: bool(read_mode_bare() == bytes([0xAA, 0x05] + [0] * 17 + [0xAA ^ 0x05]))
    ),
    _check(
        "read_mode_bare",
        lambda: bool(
            read_mode_bare()[:4] == bytes([0xAA, 0x05, 0x00, 0x00]) and read_mode_bare()[19] == (0xAA ^ 0x05)
        ),
    ),
    _check(
        "build_plant_red_blue",
        lambda: bool(
            build_plant_red_blue(6) == bytes.fromhex("330d0601" + "00" * 15 + "39")
            and build_plant_red_blue(4)[:5] == bytes([0x33, 0x0D, 0x04, 0x01, 0x00])
            and build_plant_red_blue(8, 1)[:4] == bytes([0x33, 0x0D, 0x08, 0x01])
        ),
    ),
    _check(
        "build_plant_red_blue",
        lambda: bool(
            build_plant_red_blue(80) == bytes([0x33, 0x0D, 80, 0x01] + [0] * 15 + [0x33 ^ 0x0D ^ 80 ^ 0x01])
        ),
    ),
    _check(
        "build_plant_red_blue",
        lambda: bool(
            build_plant_red_blue(6, 1) == bytes.fromhex("330d0601" + "00" * 15 + "39")
            and build_plant_red_blue(8)[:5] == bytes([0x33, 0x0D, 0x08, 0x01, 0x00])
        ),
    ),
    _check(
        "build_plant_red_blue",
        lambda: bool(
            build_plant_red_blue(80) == bytes([0x33, 0x0D, 80, 0x01] + [0] * 15 + [0x33 ^ 0x0D ^ 80 ^ 0x01])
        ),
    ),
    _check(
        "read_plant_red_blue",
        lambda: bool(read_plant_red_blue() == bytes([0xAA, 0x0D] + [0] * 17 + [0xAA ^ 0x0D])),
    ),
    _check(
        "read_plant_red_blue",
        lambda: bool(read_plant_red_blue() == bytes([0xAA, 0x0D] + [0] * 17 + [0xAA ^ 0x0D])),
    ),
    _check(
        "read_plant_red_blue",
        lambda: bool(read_plant_red_blue() == bytes([0xAA, 0x0D] + [0] * 17 + [0xAA ^ 0x0D])),
    ),
    _check(
        "read_plant_red_blue", lambda: bool(read_plant_red_blue() == bytes.fromhex("aa0d" + "00" * 17 + "a7"))
    ),
    _check(
        "build_current_limit",
        lambda: bool(
            build_current_limit(True) == bytes.fromhex("330e01000000000000000000000000000000003c")
            and build_current_limit(False)[:3] == bytes([0x33, 0x0E, 0x00])
            and build_current_limit(False)[19] == 0x3D
        ),
    ),
    _check(
        "build_current_limit",
        lambda: bool(build_current_limit(True) == bytes.fromhex("330e01000000000000000000000000000000003c")),
    ),
    _check(
        "build_current_limit",
        lambda: bool(
            build_current_limit(True) == bytes.fromhex("330e01000000000000000000000000000000003c")
            and build_current_limit(False)[:3] == bytes([0x33, 0x0E, 0x00])
        ),
    ),
    _check(
        "read_current_limit",
        lambda: bool(read_current_limit() == bytes.fromhex("aa0e0000000000000000000000000000000000a4")),
    ),
    _check(
        "read_current_limit",
        lambda: bool(read_current_limit() == bytes.fromhex("aa0e0000000000000000000000000000000000a4")),
    ),
    _check(
        "read_current_limit",
        lambda: bool(read_current_limit() == bytes.fromhex("aa0e0000000000000000000000000000000000a4")),
    ),
    _check(
        "build_light_num",
        lambda: bool(
            build_light_num(20) == bytes.fromhex("330f140000000000000000000000000000000028")
            and build_light_num(0) == bytes.fromhex("330f00000000000000000000000000000000003c")
        ),
    ),
    _check(
        "build_light_num",
        lambda: bool(
            build_light_num(2)[:4] == bytes([0x33, 0x0F, 0x02, 0x00])
            and build_light_num(2)[19] == 0x3E
            and build_light_num(4)[:3] == bytes([0x33, 0x0F, 0x04])
        ),
    ),
    _check(
        "build_light_num_h612526",
        lambda: bool(build_light_num_h612526() == bytes.fromhex("330f" + "00" * 17 + "3c")),
    ),
    _check(
        "build_light_num_h612526",
        lambda: bool(
            build_light_num_h612526() == append_bcc(bytes([0x33, 0x0F]))
            and build_light_num_h612526()[2:19] == bytes(17)
        ),
    ),
    _check(
        "read_light_num",
        lambda: bool(read_light_num() == bytes.fromhex("aa0f0000000000000000000000000000000000a5")),
    ),
    _check(
        "read_light_num",
        lambda: bool(read_light_num() == bytes.fromhex("aa0f0000000000000000000000000000000000a5")),
    ),
    _check(
        "build_multi_wifi_a1",
        lambda: bool(
            (
                lambda g: (
                    len(g) == 4
                    and g[0][:5] == bytes([0xA1, 0x11, 0x00, 0x02, 0x00])
                    and g[1][3:13]
                    == bytes([0x02]) + b"ab" + bytes([0x00, 0x02, 0xFD, 0x01, 0xE2, 0x00, 0x14])
                    and g[2][3:19] == b"/app.govee.com" + bytes(2)
                    and g[3][:3] == bytes([0xA1, 0x11, 0xFF])
                )
            )(build_multi_wifi_a1(b"ab", b"", 2, -3, 1, -30, api=b"http://app.govee.com", matter=0))
            and (
                lambda f: (
                    len(f) == 4
                    and f[0] == append_bcc(bytes([0xA1, 0x11, 0x00, 0x02, 0x00]))
                    and f[1][:8] == bytes([0xA1, 0x11, 0x01, 0x04]) + b"home"
                    and f[1][8:13] == bytes([0x04]) + b"pass"
                    and f[1][13:19] == bytes([0x02, 0x08, 0x01, 0x00, 0x00, 0x00])
                    and f[2][:5] == bytes([0xA1, 0x11, 0x02, 0x01, 0x03])
                    and f[2][5:19] == bytes(14)
                    and f[3] == append_bcc(bytes([0xA1, 0x11, 0xFF]))
                )
            )(build_multi_wifi_a1(b"home", b"pass", 2, 8, 1, 0, matter=1, security=3))
            and (
                lambda f: (
                    len(f) == 3
                    and f[0][:5] == bytes([0xA1, 0x11, 0x00, 0x01, 0x00])
                    and f[1][:14]
                    == bytes(
                        [0xA1, 0x11, 0x01, 0x01, 0x78, 0x01, 0x79, 0x01, 0x00, 0x01, 0x00, 0x00, 0x00, 0x01]
                    )
                    and f[1][14:19] == bytes(5)
                    and f[2] == append_bcc(bytes([0xA1, 0x11, 0xFF]))
                )
            )(build_multi_wifi_a1(b"x", b"y", 1, 0, 1, 0, matter=1))
            and build_multi_wifi_a1(b"x", b"y", 1, 0, 1, 0)[1][:11]
            == bytes([0xA1, 0x11, 0x01, 0x01, 0x78, 0x01, 0x79, 0x01, 0x00, 0x01, 0x00])
            and build_multi_wifi_a1(b"x", b"y", 1, 0, 1, 0)[1][11:19] == bytes(8)
            and (
                lambda f: (
                    len(f) == 5
                    and f[0][3] == 0x03
                    and f[3][3:7] == b"om" + bytes([0x01, 0x04])
                    and f[3][7:19] == bytes(12)
                )
            )(
                build_multi_wifi_a1(
                    b"n", b"p", 1, 0, 1, 0, api=b"https://device.govee.com", matter=1, security=4
                )
            )
        ),
    ),
    _check(
        "build_sleep",
        lambda: bool(build_sleep(1, 100, 30, 30)[:7] == bytes([0x33, 0x11, 0x01, 0x64, 0x1E, 0x1E, 0x00])),
    ),
    _check(
        "build_sleep_color",
        lambda: bool(
            build_sleep_color(1, 80, 30, 30, 1, 0x112233, 0x0A8C)[:13]
            == bytes([0x33, 0x11, 1, 80, 30, 30, 1, 0x11, 0x22, 0x33, 0x0A, 0x8C, 0x00])
            and build_sleep_color(1, 80, 30, 30, 1, 0x112233, 0x0A8C, kelvin_be=False)[10:12]
            == bytes([0x8C, 0x0A])
        ),
    ),
    _check(
        "build_sleep_effect",
        lambda: bool(
            build_sleep_effect(1, 80, 30, 30, True, 1, 0x1234)[:11]
            == bytes([0x33, 0x11, 1, 80, 30, 30, 1, 1, 0x34, 0x12, 0])
        ),
    ),
    _check(
        "build_sleep_effect_lc_music",
        lambda: bool(
            build_sleep_effect_lc_music(1, 80, 30, 30, True, 1, 0x1234, True, 30, 7)[:14]
            == bytes([0x33, 0x11, 1, 80, 30, 30, 1, 1, 0x34, 0x12, 1, 30, 7, 0])
        ),
    ),
    _check(
        "build_sleep_fanlight",
        lambda: bool(
            build_sleep_fanlight(1, 1, 80, 30, 30, 1, 0x112233, 0x0A8C, 1, 0, 10, 10, 6)[:19]
            == bytes([0x33, 0x11, 1, 1, 80, 30, 30, 1, 0x11, 0x22, 0x33, 0x0A, 0x8C, 1, 0, 10, 10, 6, 0])
        ),
    ),
    _check(
        "build_sleep_h6093",
        lambda: bool(
            build_sleep_h6093(1, 80, 30, 30, True, 5, 30, 1, 0x112233, True)[:15]
            == bytes([0x33, 0x11, 1, 80, 30, 30, 1, 5, 30, 1, 0x11, 0x22, 0x33, 1, 0])
        ),
    ),
    _check(
        "build_sleep_h60a0",
        lambda: bool(
            build_sleep_h60a0(1, 80, 30, 30, 1, 0x112233)[:11]
            == bytes([0x33, 0x11, 1, 80, 30, 30, 1, 0x11, 0x22, 0x33, 0x00])
        ),
    ),
    _check(
        "build_sleep_h6630",
        lambda: bool(
            build_sleep_h6630(1, 80, 30, 30, True, 1, 0x1234, True, 30, 0x01020304, 2)[:18]
            == bytes([0x33, 0x11, 1, 80, 30, 30, 1, 1, 0x34, 0x12, 1, 30, 4, 3, 2, 1, 2, 0])
        ),
    ),
    _check(
        "build_sleep_v1",
        lambda: bool(
            build_sleep_v1(1, 80, 30, 30, True, 22, 30, 1, 0xFF8000)[:14]
            == bytes([0x33, 0x11, 1, 80, 30, 30, 1, 22, 30, 1, 0xFF, 0x80, 0x00, 0x00])
        ),
    ),
    _check(
        "build_sleep_v2",
        lambda: bool(
            build_sleep_v2(1, 80, 30, 30, 1, 0xFFFFFF, 2700, True, 10, 0x0116)[:17]
            == bytes([0x33, 0x11, 1, 80, 30, 30, 1, 0xFF, 0xFF, 0xFF, 0x0A, 0x8C, 1, 10, 0x16, 0x01, 0x00])
        ),
    ),
    _check("read_sleep", lambda: bool(read_sleep() == bytes([0xAA, 0x11] + [0] * 17 + [0xBB]))),
    _check(
        "build_wakeup",
        lambda: bool(
            build_wakeup(1, 100, 7, 0, 0x7F, 30)[:9] == bytes([0x33, 0x12, 1, 100, 7, 0, 0x7F, 30, 0])
        ),
    ),
    _check(
        "build_wakeup_color",
        lambda: bool(
            build_wakeup_color(1, 100, 7, 0, 0x7F, 30, 1, 0x112233, 0x0A8C)[:15]
            == bytes([0x33, 0x12, 1, 100, 7, 0, 0x7F, 30, 1, 0x11, 0x22, 0x33, 0x0A, 0x8C, 0])
            and build_wakeup_color(1, 100, 7, 0, 0x7F, 30, 1, 0x112233, 0x0A8C, kelvin_be=False)[12:14]
            == bytes([0x8C, 0x0A])
        ),
    ),
    _check(
        "build_wakeup_effect",
        lambda: bool(
            build_wakeup_effect(1, 100, 7, 0, 0x7F, 30, True, 1, 0x1234)[:13]
            == bytes([0x33, 0x12, 1, 100, 7, 0, 0x7F, 30, 1, 1, 0x34, 0x12, 0])
        ),
    ),
    _check(
        "build_wakeup_effect_lc_music",
        lambda: bool(
            build_wakeup_effect_lc_music(1, 100, 7, 0, 0x7F, 30, True, 1, 0x1234, True, 30, 7)[:16]
            == bytes([0x33, 0x12, 1, 100, 7, 0, 0x7F, 30, 1, 1, 0x34, 0x12, 1, 30, 7, 0])
        ),
    ),
    _check(
        "build_wakeup_fanlight",
        lambda: bool(
            build_wakeup_fanlight(1, 1, 100, 7, 0, 0x7F, 30, 1, 0x112233, 0x0A8C, 1, 0, 6)[:19]
            == bytes([0x33, 0x12, 1, 1, 100, 7, 0, 0x7F, 30, 1, 0x11, 0x22, 0x33, 0x0A, 0x8C, 1, 0, 6, 0])
        ),
    ),
    _check(
        "build_wakeup_h6093",
        lambda: bool(
            build_wakeup_h6093(1, 100, 7, 0, 0x7F, 30, True, 3, 30, 1, 0x112233, True)[:17]
            == bytes([0x33, 0x12, 1, 100, 7, 0, 0x7F, 30, 1, 3, 30, 1, 0x11, 0x22, 0x33, 1, 0])
        ),
    ),
    _check(
        "build_wakeup_h60a0",
        lambda: bool(
            build_wakeup_h60a0(1, 100, 7, 0, 0x7F, 30, 1, 0x112233)[:13]
            == bytes([0x33, 0x12, 1, 100, 7, 0, 0x7F, 30, 1, 0x11, 0x22, 0x33, 0])
        ),
    ),
    _check(
        "build_wakeup_h6630",
        lambda: bool(
            build_wakeup_h6630(1, 100, 7, 0, 0x7F, 30, True, 1, 0x1234, True, 30, 0x01020304, 2)[:19]
            == bytes([0x33, 0x12, 1, 100, 7, 0, 0x7F, 30, 1, 1, 0x34, 0x12, 1, 30, 4, 3, 2, 1, 2])
        ),
    ),
    _check(
        "build_wakeup_v1",
        lambda: bool(
            build_wakeup_v1(1, 100, 7, 0, 0x7F, 30, True, 3, 30, 1, 0xFF8000)[:16]
            == bytes([0x33, 0x12, 1, 100, 7, 0, 0x7F, 30, 1, 3, 30, 1, 0xFF, 0x80, 0x00, 0x00])
        ),
    ),
    _check(
        "build_wakeup_v2",
        lambda: bool(
            build_wakeup_v2(1, 100, 7, 0, 0x7F, 30, 1, 0xFFFFFF, 6500, True, 10, 0x0203)[:19]
            == bytes(
                [0x33, 0x12, 1, 100, 7, 0, 0x7F, 30, 1, 0xFF, 0xFF, 0xFF, 0x19, 0x64, 1, 10, 0x03, 0x02, 0x00]
            )
        ),
    ),
    _check(
        "read_plug_timer_count",
        lambda: bool(read_plug_timer_count(2) == bytes([0xAA, 0x12, 0x02] + [0] * 16 + [0xBA])),
    ),
    _check("read_wakeup", lambda: bool(read_wakeup() == bytes([0xAA, 0x12] + [0] * 17 + [0xB8]))),
    _check(
        "build_direction_h6104",
        lambda: bool(
            build_direction_h6104(3)[:4] == bytes([0x33, 0x13, 0x03, 0x00])
            and build_direction_h6104(3)[19] == (0x33 ^ 0x13 ^ 0x03)
        ),
    ),
    _check(
        "build_night_mode",
        lambda: bool(
            build_night_mode(20) == bytes.fromhex("33131400" + "00" * 15 + "34")
            and build_night_mode(20, (255, 128, 0)) == bytes.fromhex("33131401ff80" + "00" * 13 + "4a")
        ),
    ),
    _check(
        "build_night_mode",
        lambda: bool(
            build_night_mode(20)[:5] == bytes([0x33, 0x13, 20, 0x00, 0x00])
            and build_night_mode(20, (255, 128, 0))[:8] == bytes([0x33, 0x13, 20, 0x01, 255, 128, 0, 0x00])
        ),
    ),
    _check(
        "build_night_mode",
        lambda: bool(
            build_night_mode(20) == bytes.fromhex("33131400" + "00" * 15 + "34")
            and build_night_mode(20, (255, 128, 0)) == bytes.fromhex("33131401ff80" + "00" * 13 + "4a")
        ),
    ),
    _check(
        "build_plug_timer_v2",
        lambda: bool(
            build_plug_timer_v2(0, 2, True, True, 7, 30, 0x7F)[:9]
            == bytes([0x33, 0x13, 0x00, 0x02, 0x81, 7, 30, 0x7F, 0x00])
            and build_plug_timer_v2(1, 0, False, True, 0, 0, 0)[2:5] == bytes([0x01, 0x00, 0x01])
        ),
    ),
    _check(
        "build_plug_timer_v2",
        lambda: bool(
            build_plug_timer_v2(0, 2, True, True, 7, 30, 0x7F)[:9]
            == bytes([0x33, 0x13, 0x00, 0x02, 0x81, 7, 30, 0x7F, 0x00])
            and build_plug_timer_v2(0, 5, True, False, 23, 59, 0x00)[2:8]
            == bytes([0x00, 0x05, 0x80, 23, 59, 0x00])
        ),
    ),
    _check("read_night_mode", lambda: bool(read_night_mode() == bytes.fromhex("aa13" + "00" * 17 + "b9"))),
    _check("read_night_mode", lambda: bool(read_night_mode() == bytes.fromhex("aa13" + "00" * 17 + "b9"))),
    _check(
        "read_night_mode_or_direction",
        lambda: bool(
            read_night_mode_or_direction()[:3] == bytes([0xAA, 0x13, 0x00])
            and read_night_mode_or_direction()[19] == (0xAA ^ 0x13)
        ),
    ),
    _check(
        "read_plug_timer_h5089",
        lambda: bool(
            read_plug_timer_h5089(2, 1)[:4] == bytes([0xAA, 0x13, 0x21, 0x00])
            and read_plug_timer_h5089(2, 1)[19] == (0xAA ^ 0x13 ^ 0x21)
        ),
    ),
    _check(
        "read_plug_timer_v2",
        lambda: bool(
            read_plug_timer_v2(1, 0)[:5] == bytes([0xAA, 0x13, 0x01, 0x00, 0x00])
            and read_plug_timer_v2(1, 2)[19] == (0xAA ^ 0x13 ^ 0x01 ^ 0x02)
        ),
    ),
    _check(
        "read_plug_timer_v2",
        lambda: bool(
            read_plug_timer_v2(0, 1)[:5] == bytes([0xAA, 0x13, 0x00, 0x01, 0x00])
            and read_plug_timer_v2(0, 1)[19] == (0xAA ^ 0x13 ^ 0x01)
        ),
    ),
    _check(
        "build_gradual",
        lambda: bool(
            build_gradual(True) == bytes([0x33, 0xA3, 0x01] + [0] * 16 + [0x91])
            and build_gradual(False) == bytes([0x33, 0xA3, 0x00] + [0] * 16 + [0x90])
        ),
    ),
    _check(
        "build_gradual",
        lambda: bool(
            build_gradual(True) == bytes([0x33, 0xA3, 0x01] + [0] * 16 + [0x91])
            and build_gradual(False) == bytes([0x33, 0xA3, 0x00] + [0] * 16 + [0x90])
        ),
    ),
    _check(
        "build_gradual",
        lambda: bool(
            build_gradual(True) == bytes([0x33, 0xA3, 0x01] + [0x00] * 16 + [0x91])
            and build_gradual(False)[:3] == bytes([0x33, 0xA3, 0x00])
            and build_gradual(False)[19] == 0x90
        ),
    ),
    _check(
        "build_gradual",
        lambda: bool(
            build_gradual(True) == bytes([0x33, 0xA3, 0x01] + [0] * 16 + [0x91])
            and build_gradual(False) == bytes([0x33, 0xA3, 0x00] + [0] * 16 + [0x90])
        ),
    ),
    _check(
        "build_gradual",
        lambda: bool(
            build_gradual(True)[:4] == bytes([0x33, 0xA3, 0x01, 0x00])
            and build_gradual(False)[:3] == bytes([0x33, 0xA3, 0x00])
            and build_gradual(True)[19] == 0x91
            and build_gradual(False)[19] == 0x90
        ),
    ),
    _check(
        "build_gradual_ble",
        lambda: bool(
            build_gradual_ble(True) == bytes([0x33, 0x14, 0x01] + [0] * 16 + [0x26])
            and build_gradual_ble(False) == bytes([0x33, 0x14, 0x00] + [0] * 16 + [0x27])
        ),
    ),
    _check(
        "build_gradual_ble",
        lambda: bool(
            build_gradual_ble(True)[:4] == bytes([0x33, 0x14, 0x01, 0x00])
            and build_gradual_ble(False)[:3] == bytes([0x33, 0x14, 0x00])
            and build_gradual_ble(True)[19] == 0x26
        ),
    ),
    _check(
        "build_gradual_ble",
        lambda: bool(
            build_gradual_ble(True) == bytes([0x33, 0x14, 0x01] + [0] * 16 + [0x26])
            and build_gradual_ble(False) == bytes([0x33, 0x14, 0x00] + [0] * 16 + [0x27])
        ),
    ),
    _check(
        "build_gradual_ble",
        lambda: bool(
            build_gradual_ble(True)[:4] == bytes([0x33, 0x14, 0x01, 0x00])
            and build_gradual_ble(True)[19] == 0x26
            and build_gradual_ble(False)[:3] == bytes([0x33, 0x14, 0x00])
            and build_gradual_ble(False)[19] == 0x27
        ),
    ),
    _check(
        "build_night_mode_page",
        lambda: bool(
            build_night_mode_page(True) == bytes([0x33, 0x14, 0x01] + [0] * 16 + [0x26])
            and build_night_mode_page(False) == bytes([0x33, 0x14, 0x00] + [0] * 16 + [0x27])
        ),
    ),
    _check(
        "build_night_mode_page_open_h601e",
        lambda: bool(
            build_night_mode_page_open_h601e()
            == [
                bytes([0x33, 0x14, 0x01] + [0] * 16 + [0x26]),
                bytes([0x33, 0x36, 0x01, 0x01] + [0] * 15 + [0x05]),
                bytes([0x33, 0x36] + [0] * 17 + [0x05]),
            ]
        ),
    ),
    _check(
        "read_wifi_mac_or_gradual",
        lambda: bool(read_wifi_mac_or_gradual() == bytes([0xAA, 0x14] + [0] * 17 + [0xBE])),
    ),
    _check(
        "read_wifi_mac_or_gradual",
        lambda: bool(read_wifi_mac_or_gradual() == bytes([0xAA, 0x14] + [0] * 17 + [0xBE])),
    ),
    _check(
        "build_h5080_timer_delete",
        lambda: bool(
            build_h5080_timer_delete(3) == bytes([0x33, 0x15, 0x00, 0x03] + [0] * 15 + [0x33 ^ 0x15 ^ 0x03])
            and build_h5080_timer_delete(3, 2)[:4] == bytes([0x33, 0x15, 0x02, 0x03])
        ),
    ),
    _check(
        "build_h5080_timer_delete",
        lambda: bool(
            build_h5080_timer_delete(3) == bytes([0x33, 0x15, 0x00, 0x03] + [0] * 15 + [0x33 ^ 0x15 ^ 0x03])
            and build_h5080_timer_delete(3, 2)[:4] == bytes([0x33, 0x15, 0x02, 0x03])
        ),
    ),
    _check(
        "build_h5086_timer_delete",
        lambda: bool(
            build_h5086_timer_delete(3) == bytes([0x33, 0x15, 0x03] + [0] * 16 + [0x33 ^ 0x15 ^ 0x03])
        ),
    ),
    _check(
        "build_h5086_timer_delete",
        lambda: bool(
            build_h5086_timer_delete(3) == bytes([0x33, 0x15, 0x03] + [0] * 16 + [0x33 ^ 0x15 ^ 0x03])
        ),
    ),
    _check(
        "build_h5086_timer_delete",
        lambda: bool(
            build_h5086_timer_delete(2)[:4] == bytes([0x33, 0x15, 0x02, 0x00])
            and build_h5086_timer_delete(2)[19] == 0x33 ^ 0x15 ^ 0x02
        ),
    ),
    _check(
        "build_plug_timer_delete",
        lambda: bool(
            build_plug_timer_delete(0, 3)
            == bytes([0x33, 0x15, 0x00, 0x03] + [0x00] * 15 + [0x33 ^ 0x15 ^ 0x03])
            and build_plug_timer_delete(1, 2)[:5] == bytes([0x33, 0x15, 0x01, 0x02, 0x00])
        ),
    ),
    _check(
        "build_plug_timer_delete",
        lambda: bool(
            build_plug_timer_delete(1, 3)[:5] == bytes([0x33, 0x15, 0x01, 0x03, 0x00])
            and build_plug_timer_delete(1, 3)[19] == 0x33 ^ 0x15 ^ 0x01 ^ 0x03
        ),
    ),
    _check("read_h6104_ip", lambda: bool(read_h6104_ip() == bytes([0xAA, 0x15] + [0] * 17 + [0xAA ^ 0x15]))),
    _check("read_h6104_ip", lambda: bool(read_h6104_ip() == bytes.fromhex("aa15" + "00" * 17 + "bf"))),
    _check("read_h6104_ip", lambda: bool(read_h6104_ip() == bytes([0xAA, 0x15] + [0] * 17 + [0xAA ^ 0x15]))),
    _check(
        "build_abs_music_16",
        lambda: bool(
            build_abs_music_16(0x1234, 80)[:7] == bytes([0x33, 0x05, 0x16, 0x34, 0x12, 0x50, 0x00])
            and build_abs_music_16(300)[:6] == bytes([0x33, 0x05, 0x16, 0x2C, 0x01, 0x63])
        ),
    ),
    _check(
        "build_abs_music_16",
        lambda: bool(
            build_abs_music_16(0x1234, 80)[:7] == bytes([0x33, 0x05, 0x16, 0x34, 0x12, 0x50, 0x00])
            and build_abs_music_16(300, 0xFF)[:6] == bytes([0x33, 0x05, 0x16, 0x2C, 0x01, 0xFF])
            and build_abs_music_16(0, 0xFF)[:6] == bytes([0x33, 0x05, 0x16, 0x00, 0x00, 0xFF])
        ),
    ),
    _check(
        "build_effect_direction",
        lambda: bool(build_effect_direction(2) == bytes.fromhex("3316020000000000000000000000000000000027")),
    ),
    _check(
        "build_effect_direction",
        lambda: bool(
            build_effect_direction(2)[:4] == bytes([0x33, 0x16, 0x02, 0x00])
            and build_effect_direction(2)[19] == (0x33 ^ 0x16 ^ 0x02)
        ),
    ),
    _check(
        "build_energy_saving",
        lambda: bool(
            build_energy_saving(True) == bytes.fromhex("3316010000000000000000000000000000000024")
            and build_energy_saving(False)[:3] == bytes([0x33, 0x16, 0x00])
        ),
    ),
    _check(
        "build_energy_saving",
        lambda: bool(
            build_energy_saving(True)[:4] == bytes([0x33, 0x16, 0x01, 0x00])
            and build_energy_saving(False)[:3] == bytes([0x33, 0x16, 0x00])
            and build_energy_saving(False)[19] == (0x33 ^ 0x16)
        ),
    ),
    _check(
        "build_energy_saving",
        lambda: bool(
            build_energy_saving(True)[:4] == bytes([0x33, 0x16, 0x01, 0x00])
            and build_energy_saving(False)[:3] == bytes([0x33, 0x16, 0x00])
            and build_energy_saving(True)[19] == (0x33 ^ 0x16 ^ 0x01)
        ),
    ),
    _check(
        "build_energy_saving",
        lambda: bool(
            build_energy_saving(True) == bytes.fromhex("3316010000000000000000000000000000000024")
            and build_energy_saving(False) == bytes.fromhex("3316000000000000000000000000000000000025")
        ),
    ),
    _check(
        "build_light_indicator",
        lambda: bool(
            build_light_indicator(True, 22, 0, 7, 30)[:8] == bytes([0x33, 0x16, 0x01, 22, 0, 7, 30, 0x00])
            and build_light_indicator(False, forever=True)[:8]
            == bytes([0x33, 0x16, 0x00, 0xFF, 0xFF, 0xFF, 0xFF, 0x00])
        ),
    ),
    _check(
        "build_light_indicator",
        lambda: bool(
            build_light_indicator(True, 22, 0, 7, 30)[:8] == bytes([0x33, 0x16, 0x01, 22, 0, 7, 30, 0x00])
            and build_light_indicator(False, forever=True)[:7]
            == bytes([0x33, 0x16, 0x00, 0xFF, 0xFF, 0xFF, 0xFF])
            and build_light_indicator(True, forever=True)[19] == (0x33 ^ 0x16 ^ 0x01)
        ),
    ),
    _check(
        "build_light_indicator",
        lambda: bool(
            build_light_indicator(True, 22, 0, 7, 30)[:8] == bytes([0x33, 0x16, 0x01, 22, 0, 7, 30, 0x00])
            and build_light_indicator(False, forever=True)[:7]
            == bytes([0x33, 0x16, 0x00, 0xFF, 0xFF, 0xFF, 0xFF])
            and build_light_indicator(True, forever=True)[19] == (0x33 ^ 0x16 ^ 0x01)
        ),
    ),
    _check(
        "build_light_indicator",
        lambda: bool(
            build_light_indicator(True, 22, 0, 7, 30)[:8] == bytes([0x33, 0x16, 0x01, 22, 0, 7, 30, 0x00])
            and build_light_indicator(False, forever=True)[:8]
            == bytes([0x33, 0x16, 0x00, 0xFF, 0xFF, 0xFF, 0xFF, 0x00])
            and build_light_indicator(True, forever=True)[19] == 0x24
            and build_light_indicator(False, 23, 0, 6, 0)[19] == (0x33 ^ 0x16 ^ 23 ^ 6)
        ),
    ),
    _check(
        "build_plug_not_disturb_h5086",
        lambda: bool(
            build_plug_not_disturb_h5086(True, 22, 0, 7, 0, False)[:9]
            == bytes([0x33, 0x16, 0x01, 22, 0, 7, 0, 0x00, 0x00])
            and build_plug_not_disturb_h5086(False, 0, 0, 23, 59, True)[:8]
            == bytes([0x33, 0x16, 0x00, 0, 0, 23, 59, 0x01])
        ),
    ),
    _check(
        "build_plug_not_disturb_h5086",
        lambda: bool(
            build_plug_not_disturb_h5086(True, 22, 0, 7, 0, False)[:9]
            == bytes([0x33, 0x16, 0x01, 22, 0, 7, 0, 0x00, 0x00])
            and build_plug_not_disturb_h5086(True, 0, 0, 23, 59, True)[:9]
            == bytes([0x33, 0x16, 0x01, 0, 0, 23, 59, 0x01, 0x00])
        ),
    ),
    _check(
        "read_light_indicator",
        lambda: bool(
            read_light_indicator()[:3] == bytes([0xAA, 0x16, 0x00])
            and read_light_indicator()[19] == (0xAA ^ 0x16)
        ),
    ),
    _check(
        "read_light_indicator",
        lambda: bool(read_light_indicator() == bytes.fromhex("aa160000000000000000000000000000000000bc")),
    ),
    _check(
        "read_light_indicator",
        lambda: bool(
            read_light_indicator()[:3] == bytes([0xAA, 0x16, 0x00])
            and read_light_indicator()[19] == (0xAA ^ 0x16)
        ),
    ),
    _check(
        "read_light_indicator",
        lambda: bool(read_light_indicator() == bytes([0xAA, 0x16] + [0] * 17 + [0xAA ^ 0x16])),
    ),
    _check(
        "read_light_indicator",
        lambda: bool(read_light_indicator() == bytes.fromhex("aa160000000000000000000000000000000000bc")),
    ),
    _check(
        "build_plug_pro_exception_state",
        lambda: bool(build_plug_pro_exception_state() == bytes([0x33, 0x17, 0x01] + [0x00] * 16 + [0x25])),
    ),
    _check(
        "build_wifi_link_start",
        lambda: bool(
            build_wifi_link_start(True)[:3] == bytes([0x33, 0x17, 0x01])
            and build_wifi_link_start(False)[:3] == bytes([0x33, 0x17, 0x00])
            and build_wifi_link_start(True)[19] == 0x25
            and build_wifi_link_start(False)[19] == 0x24
        ),
    ),
    _check(
        "read_plug_pro_exception_state",
        lambda: bool(read_plug_pro_exception_state() == bytes([0xAA, 0x17] + [0x00] * 17 + [0xBD])),
    ),
    _check(
        "read_h5086_electric",
        lambda: bool(read_h5086_electric() == bytes([0xAA, 0x19] + [0] * 17 + [0xAA ^ 0x19])),
    ),
    _check(
        "read_h5086_electric",
        lambda: bool(
            read_h5086_electric()[:3] == bytes([0xAA, 0x19, 0x00])
            and read_h5086_electric()[19] == 0xAA ^ 0x19
        ),
    ),
    _check(
        "read_h5086_electric",
        lambda: bool(read_h5086_electric() == bytes([0xAA, 0x19] + [0] * 17 + [0xAA ^ 0x19])),
    ),
    _check(
        "build_h5086_time_monitor",
        lambda: bool(
            build_h5086_time_monitor(True, 7, 30, 0x01020304)[:10]
            == bytes([0x33, 0x1A, 0x01, 7, 30, 0x01, 0x02, 0x03, 0x04, 0x00])
        ),
    ),
    _check(
        "build_h5086_time_monitor",
        lambda: bool(
            build_h5086_time_monitor(True, 1, 30, 0x01020304)[:10]
            == bytes([0x33, 0x1A, 0x01, 0x01, 0x1E, 0x01, 0x02, 0x03, 0x04, 0x00])
            and build_h5086_time_monitor(True, 1, 30, 0x01020304)[19]
            == 0x33 ^ 0x1A ^ 0x01 ^ 0x01 ^ 0x1E ^ 0x01 ^ 0x02 ^ 0x03 ^ 0x04
        ),
    ),
    _check(
        "build_h5086_time_monitor",
        lambda: bool(
            build_h5086_time_monitor(True, 7, 30, 0x01020304)[:9]
            == bytes([0x33, 0x1A, 0x01, 7, 30, 0x01, 0x02, 0x03, 0x04])
            and build_h5086_time_monitor(True, 7, 30, 0x01020304)[19]
            == (0x33 ^ 0x1A ^ 0x01 ^ 7 ^ 30 ^ 0x01 ^ 0x02 ^ 0x03 ^ 0x04)
        ),
    ),
    _check(
        "read_h5086_time_monitor",
        lambda: bool(read_h5086_time_monitor() == bytes([0xAA, 0x1A] + [0] * 17 + [0xAA ^ 0x1A])),
    ),
    _check(
        "read_h5086_time_monitor",
        lambda: bool(
            read_h5086_time_monitor()[:3] == bytes([0xAA, 0x1A, 0x00])
            and read_h5086_time_monitor()[19] == 0xAA ^ 0x1A
        ),
    ),
    _check(
        "read_h5086_time_monitor",
        lambda: bool(read_h5086_time_monitor() == bytes([0xAA, 0x1A] + [0] * 17 + [0xAA ^ 0x1A])),
    ),
    _check(
        "build_h5089_nl_brightness",
        lambda: bool(build_h5089_nl_brightness(80)[:6] == bytes([0x33, 0x1B, 0x01, 0x02, 0x50, 0x00])),
    ),
    _check(
        "build_h5089_nl_brightness",
        lambda: bool(
            build_h5089_nl_brightness(80)[:6] == bytes([0x33, 0x1B, 0x01, 0x02, 0x50, 0x00])
            and build_h5089_nl_brightness(80)[19] == 0x33 ^ 0x1B ^ 0x01 ^ 0x02 ^ 0x50
        ),
    ),
    _check(
        "build_h5089_nl_switch",
        lambda: bool(
            build_h5089_nl_switch(True)[:6] == bytes([0x33, 0x1B, 0x01, 0x01, 0x01, 0x00])
            and build_h5089_nl_switch(False)[19] == 0x33 ^ 0x1B ^ 0x01 ^ 0x01
        ),
    ),
    _check(
        "build_h5089_nl_switch",
        lambda: bool(
            build_h5089_nl_switch(True)[:6] == bytes([0x33, 0x1B, 0x01, 0x01, 0x01, 0x00])
            and build_h5089_nl_switch(False)[:5] == bytes([0x33, 0x1B, 0x01, 0x01, 0x00])
        ),
    ),
    _check(
        "build_nl71xx_brightness",
        lambda: bool(
            build_nl71xx_brightness(100)[:6] == bytes([0x3A, 0x1B, 0x01, 0x02, 0x64, 0x00])
            and build_nl71xx_brightness(100)[19] == 0x3A ^ 0x1B ^ 0x01 ^ 0x02 ^ 0x64
        ),
    ),
    _check(
        "build_nl71xx_switch",
        lambda: bool(
            build_nl71xx_switch(True)[:6] == bytes([0x3A, 0x1B, 0x01, 0x01, 0x01, 0x00])
            and build_nl71xx_switch(True)[19] == 0x3A ^ 0x1B ^ 0x01 ^ 0x01 ^ 0x01
        ),
    ),
    _check(
        "build_nl_mode_color",
        lambda: bool(
            build_nl_mode_color(0xFF, 0x8D, 0x0B)[:10]
            == bytes([0x3A, 0x1B, 0x05, 0x0D, 0xFF, 0x8D, 0x0B, 0x00, 0x00, 0x00])
            and build_nl_mode_color(0xFF, 0x33, 0x00, kelvin=1000)[4:9]
            == bytes([0xFF, 0x33, 0x00, 0x03, 0xE8])
            and build_nl_mode_color(0xFF, 0x33, 0x00, kelvin=1000)[19]
            == 0x3A ^ 0x1B ^ 0x05 ^ 0x0D ^ 0xFF ^ 0x33 ^ 0x00 ^ 0x03 ^ 0xE8
        ),
    ),
    _check(
        "build_nl_mode_default",
        lambda: bool(
            build_nl_mode_default()[:5] == bytes([0x3A, 0x1B, 0x05, 0xFC, 0x00])
            and build_nl_mode_default()[19] == 0x3A ^ 0x1B ^ 0x05 ^ 0xFC
        ),
    ),
    _check(
        "build_nl_mode_diy",
        lambda: bool(
            build_nl_mode_diy()[:7] == bytes([0x3A, 0x1B, 0x05, 0x0A, 0x00, 0x00, 0x00])
            and build_nl_mode_diy()[19] == 0x3A ^ 0x1B ^ 0x05 ^ 0x0A
        ),
    ),
    _check(
        "build_nl_mode_scene",
        lambda: bool(
            build_nl_mode_scene(7)[:7] == bytes([0x3A, 0x1B, 0x05, 0x13, 0x00, 0x07, 0x00])
            and build_nl_mode_scene(7)[19] == 0x3A ^ 0x1B ^ 0x05 ^ 0x13 ^ 0x07
        ),
    ),
    _check(
        "read_nl_mode",
        lambda: bool(
            read_nl_mode()[:4] == bytes([0xAA, 0x1B, 0x05, 0x00]) and read_nl_mode()[19] == 0xAA ^ 0x1B ^ 0x05
        ),
    ),
    _check(
        "read_nl_switch",
        lambda: bool(
            read_nl_switch()[:4] == bytes([0xAA, 0x1B, 0x01, 0x00])
            and read_nl_switch()[19] == 0xAA ^ 0x1B ^ 0x01
        ),
    ),
    _check(
        "read_nl_switch",
        lambda: bool(
            read_nl_switch()[:4] == bytes([0xAA, 0x1B, 0x01, 0x00])
            and read_nl_switch()[19] == 0xAA ^ 0x1B ^ 0x01
        ),
    ),
    _check(
        "build_plug_child_lock",
        lambda: bool(
            build_plug_child_lock(True)
            == bytes([0x33, 0x1F, 0x02, 0x01] + [0] * 15 + [0x33 ^ 0x1F ^ 0x02 ^ 0x01])
        ),
    ),
    _check(
        "build_plug_child_lock",
        lambda: bool(
            build_plug_child_lock(True)[:5] == bytes([0x33, 0x1F, 0x02, 0x01, 0x00])
            and build_plug_child_lock(False)[19] == 0x33 ^ 0x1F ^ 0x02
        ),
    ),
    _check(
        "build_plug_child_lock",
        lambda: bool(
            build_plug_child_lock(True)
            == bytes([0x33, 0x1F, 0x02, 0x01] + [0] * 15 + [0x33 ^ 0x1F ^ 0x02 ^ 0x01])
            and build_plug_child_lock(False)[:4] == bytes([0x33, 0x1F, 0x02, 0x00])
        ),
    ),
    _check(
        "read_h5086_child_lock",
        lambda: bool(read_h5086_child_lock() == bytes([0xAA, 0x1F] + [0] * 17 + [0xAA ^ 0x1F])),
    ),
    _check(
        "read_h5086_child_lock",
        lambda: bool(
            read_h5086_child_lock()[:3] == bytes([0xAA, 0x1F, 0x00])
            and read_h5086_child_lock()[19] == 0xAA ^ 0x1F
        ),
    ),
    _check(
        "read_h5086_child_lock",
        lambda: bool(read_h5086_child_lock() == bytes([0xAA, 0x1F] + [0] * 17 + [0xAA ^ 0x1F])),
    ),
    _check(
        "read_h5089_child_lock",
        lambda: bool(read_h5089_child_lock() == bytes([0xAA, 0x1F, 0x02] + [0] * 16 + [0xAA ^ 0x1F ^ 0x02])),
    ),
    _check(
        "read_h5089_child_lock",
        lambda: bool(read_h5089_child_lock() == bytes([0xAA, 0x1F, 0x02] + [0] * 16 + [0xAA ^ 0x1F ^ 0x02])),
    ),
    _check(
        "read_plug_child_lock",
        lambda: bool(
            read_plug_child_lock()[:4] == bytes([0xAA, 0x1F, 0x02, 0x00])
            and read_plug_child_lock()[19] == 0xAA ^ 0x1F ^ 0x02
        ),
    ),
    _check(
        "build_breath_train_mode_20",
        lambda: bool(
            build_breath_train_mode_20(True)[:5] == bytes([0x33, 0x05, 0x20, 0x01, 0x00])
            and build_breath_train_mode_20(False)[19] == (0x33 ^ 0x05 ^ 0x20 ^ 0x02)
        ),
    ),
    _check(
        "build_breath_train_mode_20",
        lambda: bool(
            build_breath_train_mode_20(True)[:5] == bytes([0x33, 0x05, 0x20, 0x01, 0x00])
            and build_breath_train_mode_20(False)[:5] == bytes([0x33, 0x05, 0x20, 0x02, 0x00])
            and build_breath_train_mode_20(False)[19] == (0x33 ^ 0x05 ^ 0x20 ^ 0x02)
        ),
    ),
    _check(
        "read_wifi_hard_version",
        lambda: bool(read_wifi_hard_version() == bytes([0xAA, 0x20] + [0] * 17 + [0x8A])),
    ),
    _check(
        "build_h5127_sync_triggers",
        lambda: bool(
            build_h5127_sync_triggers([])
            == [
                bytes([0x33, 0x21, 0x00, 0x01] + [0] * 15 + [0x13]),
                bytes([0x33, 0x21, 0x01] + [0xFF] * 16 + [0x13]),
                bytes([0x33, 0x21, 0xFF] + [0] * 16 + [0xED]),
            ]
            and build_h5127_sync_triggers([(1, 0x0102)], 5)[0][:7]
            == bytes([0x33, 0x21, 0x00, 0x01, 0x01, 0x01, 0x05])
            and build_h5127_sync_triggers([(1, 0x0102)], 5)[1][:19]
            == bytes([0x33, 0x21, 0x01, 0x01, 0x01, 0x02] + [0xFF] * 12 + [0x00])
            and len(build_h5127_sync_triggers([(2, 7)] * 6)) == 4
        ),
    ),
    _check(
        "read_wifi_soft_version",
        lambda: bool(read_wifi_soft_version() == bytes([0xAA, 0x21] + [0] * 17 + [0x8B])),
    ),
    _check(
        "read_wifi_soft_version",
        lambda: bool(read_wifi_soft_version() == bytes([0xAA, 0x21] + [0] * 17 + [0x8B])),
    ),
    _check(
        "read_wifi_soft_version",
        lambda: bool(read_wifi_soft_version() == bytes([0xAA, 0x21] + [0] * 17 + [0x8B])),
    ),
    _check(
        "read_wifi_dsp_version",
        lambda: bool(read_wifi_dsp_version() == bytes([0xAA, 0x22] + [0] * 17 + [0x88])),
    ),
    _check(
        "read_wifi_dsp_version",
        lambda: bool(read_wifi_dsp_version() == bytes([0xAA, 0x22] + [0] * 17 + [0x88])),
    ),
    _check(
        "build_new_timer",
        lambda: bool(
            build_new_timer(2, True, True, 6, 45, 0x7F)[:8]
            == bytes([0x33, 0x23, 0x02, 0x81, 0x06, 0x2D, 0x7F, 0x00])
            and build_new_timer(0, True, False, 23, 0, 0xFF)[3] == 0x80
            and build_new_timer(3, False, True, 12, 0, 0x80)[2:7] == bytes([0x03, 0x01, 0x0C, 0x00, 0x80])
        ),
    ),
    _check(
        "read_new_timer",
        lambda: bool(
            read_new_timer() == bytes([0xAA, 0x23, 0xFF] + [0] * 16 + [0x76])
            and read_new_timer(1)[:4] == bytes([0xAA, 0x23, 0x01, 0x00])
        ),
    ),
    _check(
        "read_new_timer",
        lambda: bool(
            read_new_timer() == bytes([0xAA, 0x23, 0xFF] + [0] * 16 + [0x76])
            and read_new_timer(1)[:4] == bytes([0xAA, 0x23, 0x01, 0x00])
        ),
    ),
    _check(
        "build_h682x_unbind",
        lambda: bool(build_h682x_unbind() == bytes.fromhex("3324010000000000000000000000000000000016")),
    ),
    _check(
        "build_ic_points_h61d3",
        lambda: bool(
            build_ic_points_h61d3([(1, 2)])
            == [
                bytes.fromhex("a300010224000100010002000000000000000086"),
                bytes.fromhex("a3ff00000000000000000000000000000000005c"),
            ]
            and build_ic_points_h61d3([(i, 0x100 + i) for i in range(10)])
            == [
                bytes.fromhex("a300010324000a0000010000010101000201028e"),
                bytes.fromhex("a3010003010300040104000501050006010600a2"),
                bytes.fromhex("a3ff07010700080108000901090000000000005d"),
            ]
        ),
    ),
    _check(
        "build_ic_points_h61d3",
        lambda: bool(
            build_ic_points_h61d3([(1, 2)])[0][:11]
            == bytes([0xA3, 0x00, 0x01, 0x02, 0x24, 0x00, 0x01, 0x00, 0x01, 0x00, 0x02])
            and build_ic_points_h61d3([(1, 2)])[1][:2] == bytes([0xA3, 0xFF])
            and len(build_ic_points_h61d3([(i, i) for i in range(10)])) == 3
            and build_ic_points_h61d3([(i, i) for i in range(10)])[0][3] == 3
            and build_ic_points_h61d3([(i, i) for i in range(10)])[1][:2] == bytes([0xA3, 0x01])
        ),
    ),
    _check(
        "build_set_light_start",
        lambda: bool(
            build_set_light_start(0, 500) == bytes.fromhex("33240101f40000000000000000000000000000e3")
        ),
    ),
    _check(
        "build_set_light_start",
        lambda: bool(
            build_set_light_start(0, 500) == bytes.fromhex("33240101f40000000000000000000000000000e3")
            and build_set_light_start(3, 0x1234)[:5] == bytes([0x33, 0x24, 0x03, 0x12, 0x34])
            and build_set_light_start(300, 70000)[2:5] == bytes([0x2C, 0x11, 0x70])
            and build_set_light_start(1, -1)[3:5] == bytes([0xFF, 0xFF])
        ),
    ),
    _check(
        "read_ic_point",
        lambda: bool(read_ic_point() == bytes.fromhex("aa2400000000000000000000000000000000008e")),
    ),
    _check("read_ic_point", lambda: bool(read_ic_point() == bytes([0xAA, 0x24] + [0x00] * 17 + [0x8E]))),
    _check(
        "read_ic_point",
        lambda: bool(read_ic_point() == bytes.fromhex("aa2400000000000000000000000000000000008e")),
    ),
    _check(
        "read_ic_point",
        lambda: bool(
            read_ic_point() == append_bcc(bytes([0xAA, 0x24])) and read_ic_point()[2:19] == bytes(17)
        ),
    ),
    _check(
        "read_preset_info",
        lambda: bool(read_preset_info() == bytes([0xAA, 0x24]) + bytes(17) + bytes([0xAA ^ 0x24])),
    ),
    _check(
        "build_set_light_end",
        lambda: bool(build_set_light_end() == bytes.fromhex("332501" + "00" * 16 + "17")),
    ),
    _check(
        "read_preset_color",
        lambda: bool(
            read_preset_color(2) == bytes([0xAA, 0x25, 0x02]) + bytes(16) + bytes([0xAA ^ 0x25 ^ 0x02])
        ),
    ),
    _check(
        "build_color_tem_type", lambda: bool(build_color_tem_type(2)[:4] == bytes([0x33, 0x26, 0x02, 0x00]))
    ),
    _check(
        "build_take_photo_start",
        lambda: bool(
            build_take_photo_start()[:8] == bytes([0x33, 0x26, 0x01, 0x64, 0xFF, 0xFF, 0x00, 0x00])
            and build_take_photo_start()[19] == 0x33 ^ 0x26 ^ 0x01 ^ 0x64
        ),
    ),
    _check(
        "build_take_photo_stop",
        lambda: bool(
            build_take_photo_stop()[:4] == bytes([0x33, 0x26, 0x00, 0x00])
            and build_take_photo_stop()[19] == 0x33 ^ 0x26
        ),
    ),
    _check(
        "read_color_tem_type",
        lambda: bool(
            read_color_tem_type()[:3] == bytes([0xAA, 0x26, 0x00])
            and read_color_tem_type()[19] == 0xAA ^ 0x26
        ),
    ),
    _check(
        "read_h6057_preset_scene_page",
        lambda: bool(read_h6057_preset_scene_page(1)[:4] == bytes([0xAA, 0x26, 0x01, 0x00])),
    ),
    _check(
        "read_preset_scene",
        lambda: bool(
            read_preset_scene(1) == bytes([0xAA, 0x26, 0x01]) + bytes(16) + bytes([0xAA ^ 0x26 ^ 0x01])
        ),
    ),
    _check(
        "build_h6057_preview_effect",
        lambda: bool(
            build_h6057_preview_effect(0, 80, 5, 60, 1, 0xFF, 0x80, 0x00, True)
            == bytes.fromhex("33270050053c01ff800001000000000000000002")
            and build_h6057_preview_effect(1, 10, 0, 255, 0, 1, 2, 3, False)
            == bytes.fromhex("3327010a00ff00010203000000000000000000e0")
        ),
    ),
    _check(
        "build_h6057_preview_effect",
        lambda: bool(
            build_h6057_preview_effect(0, 80, 5, 60, 1, 0xFF, 0x80, 0x00, True)[:12]
            == bytes([0x33, 0x27, 0x00, 0x50, 0x05, 0x3C, 0x01, 0xFF, 0x80, 0x00, 0x01, 0x00])
            and build_h6057_preview_effect(1, 10, 0, 255, 0, 0, 0, 0, False)[2:11]
            == bytes([0x01, 0x0A, 0x00, 0xFF, 0x00, 0x00, 0x00, 0x00, 0x00])
        ),
    ),
    _check(
        "build_h6057_preview_effect",
        lambda: bool(
            build_h6057_preview_effect(0, 80, 5, 60, 1, 0xFF, 0x80, 0x00, True)[:12]
            == bytes([0x33, 0x27, 0x00, 0x50, 0x05, 0x3C, 0x01, 0xFF, 0x80, 0x00, 0x01, 0x00])
        ),
    ),
    _check(
        "build_power_warning",
        lambda: bool(
            build_power_warning(True, 1500)[:6] == bytes([0x33, 0x27, 0x01, 0x05, 0xDC, 0x00])
            and build_power_warning(None, None)[2:5] == bytes([0xFF, 0xFF, 0xFF])
            and build_power_warning(False, 0)[2:5] == bytes([0x00, 0x00, 0x00])
        ),
    ),
    _check(
        "build_power_warning",
        lambda: bool(
            build_power_warning(True, 1500)[:6] == bytes([0x33, 0x27, 0x01, 0x05, 0xDC, 0x00])
            and build_power_warning(None, None)[2:6] == bytes([0xFF, 0xFF, 0xFF, 0x00])
            and build_power_warning(False, 10)[2:5] == bytes([0x00, 0x00, 0x0A])
        ),
    ),
    _check(
        "build_wash_remind",
        lambda: bool(
            build_wash_remind(True, 336)[:6] == bytes([0x3A, 0x27, 0x01, 0x01, 0x50, 0x00])
            and build_wash_remind(False, 336, reset=True)[:3] == bytes([0x3A, 0x27, 0x02])
            and build_wash_remind(True, 336, pro_type=0x33)[:2] == bytes([0x33, 0x27])
        ),
    ),
    _check("read_op27", lambda: bool(read_op27() == bytes([0xAA, 0x27]) + bytes(17) + bytes([0xAA ^ 0x27]))),
    _check(
        "read_power_warning",
        lambda: bool(
            read_power_warning()[:3] == bytes([0xAA, 0x27, 0x00]) and read_power_warning()[19] == 0xAA ^ 0x27
        ),
    ),
    _check(
        "build_delete_preset_scene",
        lambda: bool(
            build_delete_preset_scene(0x1234)[:5] == bytes([0x33, 0x28, 0x34, 0x12, 0x00])
            and build_delete_preset_scene(0x1234)[19] == 0x33 ^ 0x28 ^ 0x34 ^ 0x12
        ),
    ),
    _check(
        "build_h5089_sleep",
        lambda: bool(
            build_h5089_sleep(1, 100, 30, 30, 1, 0xFF, 0x00, 0x10)[:13]
            == bytes([0x33, 0x28, 0x01, 0x64, 0x1E, 0x1E, 0x01, 0xFF, 0x00, 0x10, 0x00, 0x00, 0x00])
            and build_h5089_sleep(1, 50, 30, 0, 1, 0xFF, 0x33, 0x00, kelvin=1000)[7:12]
            == bytes([0xFF, 0x33, 0x00, 0xE8, 0x03])
        ),
    ),
    _check(
        "build_h5089_sleep",
        lambda: bool(
            build_h5089_sleep(1, 100, 30, 30, 0, 0xFF, 0x00, 0x10)[:13]
            == bytes([0x33, 0x28, 0x01, 0x64, 0x1E, 0x1E, 0x00, 0xFF, 0x00, 0x10, 0x00, 0x00, 0x00])
        ),
    ),
    _check(
        "build_power_warning_v2",
        lambda: bool(
            build_power_warning_v2(2, True, 2000)[:7] == bytes([0x33, 0x28, 0x02, 0x01, 0x07, 0xD0, 0x00])
            and build_power_warning_v2(1, False, None)[2:6] == bytes([0x01, 0x00, 0xFF, 0xFF])
        ),
    ),
    _check(
        "read_h5089_sleep",
        lambda: bool(read_h5089_sleep() == bytes([0xAA, 0x28]) + bytes(17) + bytes([0xAA ^ 0x28])),
    ),
    _check(
        "read_h5089_sleep",
        lambda: bool(
            read_h5089_sleep()[:3] == bytes([0xAA, 0x28, 0x00]) and read_h5089_sleep()[19] == 0xAA ^ 0x28
        ),
    ),
    _check(
        "build_h5089_wakeup",
        lambda: bool(
            build_h5089_wakeup(1, 100, 7, 30, 0x7F, 15, 1, 0x10, 0x20, 0x30)[:15]
            == bytes(
                [0x33, 0x29, 0x01, 0x64, 0x07, 0x1E, 0x7F, 0x0F, 0x01, 0x10, 0x20, 0x30, 0x00, 0x00, 0x00]
            )
            and build_h5089_wakeup(1, 50, 30, 75, 0, 0, 1, 0, 0, 0)[4:6] == bytes([23, 59])
        ),
    ),
    _check(
        "build_h5089_wakeup",
        lambda: bool(
            build_h5089_wakeup(1, 100, 7, 30, 0x7F, 15, 1, 0x10, 0x20, 0x30, 2700)[:15]
            == bytes(
                [0x33, 0x29, 0x01, 0x64, 0x07, 0x1E, 0x7F, 0x0F, 0x01, 0x10, 0x20, 0x30, 0x8C, 0x0A, 0x00]
            )
            and build_h5089_wakeup(1, 100, 30, 70, 0, 0, 0, 0, 0, 0)[4:6] == bytes([0x17, 0x3B])
        ),
    ),
    _check(
        "build_sort_preset_scenes",
        lambda: bool(
            build_sort_preset_scenes([0x0102, 0x0304])[:8]
            == bytes([0x33, 0x29, 0x02, 0x02, 0x01, 0x04, 0x03, 0x00])
            and build_sort_preset_scenes([1] * 8)[:3] == bytes([0x33, 0x29, 0x08])
            and build_sort_preset_scenes([1] * 8)[17:19] == bytes([0x01, 0x00])
        ),
    ),
    _check(
        "read_h5089_wakeup",
        lambda: bool(read_h5089_wakeup() == bytes([0xAA, 0x29]) + bytes(17) + bytes([0xAA ^ 0x29])),
    ),
    _check(
        "read_h5089_wakeup",
        lambda: bool(
            read_h5089_wakeup()[:3] == bytes([0xAA, 0x29, 0x00]) and read_h5089_wakeup()[19] == 0xAA ^ 0x29
        ),
    ),
    _check(
        "build_h6057_light_switch",
        lambda: bool(
            build_h6057_light_switch(True)[:4] == bytes([0x33, 0x30, 0x01, 0x00])
            and build_h6057_light_switch(False)[:3] == bytes([0x33, 0x30, 0x00])
            and build_h6057_light_switch(True)[19] == 0x02
            and build_h6057_light_switch(False)[19] == 0x03
        ),
    ),
    _check(
        "build_light_direction",
        lambda: bool(
            build_light_direction(2) == bytes([0x33, 0x30, 0x02] + [0] * 16 + [0x33 ^ 0x30 ^ 0x02])
            and build_light_direction(5)[19] == 0x33 ^ 0x30 ^ 0x05
        ),
    ),
    _check(
        "build_light_direction", lambda: bool(build_light_direction(2)[:4] == bytes([0x33, 0x30, 0x02, 0x00]))
    ),
    _check(
        "build_light_direction",
        lambda: bool(
            build_light_direction(2) == append_bcc(bytes([0x33, 0x30, 0x02]))
            and build_light_direction(2)[:4] == bytes([0x33, 0x30, 0x02, 0x00])
            and build_light_direction(2)[19] == (0x33 ^ 0x30 ^ 0x02)
        ),
    ),
    _check(
        "build_light_direction_exit_calibration",
        lambda: bool(
            build_light_direction_exit_calibration(2)[0][:4] == bytes([0x33, 0x30, 0x02, 0x00])
            and build_light_direction_exit_calibration(2)[1][:5] == bytes([0x33, 0x44, 0x02, 0x00, 0x00])
            and build_light_direction_exit_calibration(2)[1][19] == 0x33 ^ 0x44 ^ 0x02
        ),
    ),
    _check(
        "build_light_switch_h6057",
        lambda: bool(
            build_light_switch_h6057(True)[:4] == bytes([0x33, 0x30, 0x01, 0x00])
            and build_light_switch_h6057(True)[19] == 0x02
            and build_light_switch_h6057(False) == bytes([0x33, 0x30] + [0] * 17 + [0x03])
        ),
    ),
    _check(
        "build_light_switch_h6057",
        lambda: bool(
            build_light_switch_h6057(True)[:4] == bytes([0x33, 0x30, 0x01, 0x00])
            and build_light_switch_h6057(False)[:3] == bytes([0x33, 0x30, 0x00])
            and build_light_switch_h6057(False)[2:19] == bytes(17)
        ),
    ),
    _check(
        "build_op30_byte",
        lambda: bool(
            build_op30_byte(5)[:4] == bytes([0x33, 0x30, 0x05, 0x00])
            and build_op30_byte(5)[19] == 0x33 ^ 0x30 ^ 0x05
        ),
    ),
    _check(
        "build_zone_power",
        lambda: bool(
            build_zone_power(1, True)[:5] == bytes([0x33, 0x30, 0x01, 0x01, 0x00])
            and build_zone_power(2, False)[:4] == bytes([0x33, 0x30, 0x02, 0x00])
            and build_zone_power(0, True)[19] == 0x33 ^ 0x30 ^ 0x01
        ),
    ),
    _check(
        "build_zone_power",
        lambda: bool(
            build_zone_power(1, True)[:5] == bytes([0x33, 0x30, 0x01, 0x01, 0x00])
            and build_zone_power(2, False)[:4] == bytes([0x33, 0x30, 0x02, 0x00])
            and build_zone_power(1, True)[19] == 0x33 ^ 0x30
        ),
    ),
    _check(
        "build_zone_power",
        lambda: bool(
            build_zone_power(1, True)[:5] == bytes([0x33, 0x30, 0x01, 0x01, 0x00])
            and build_zone_power(0, False)[:4] == bytes([0x33, 0x30, 0x00, 0x00])
            and build_zone_power(2, True)[:4] == bytes([0x33, 0x30, 0x02, 0x01])
        ),
    ),
    _check(
        "build_zone_power",
        lambda: bool(
            build_zone_power(1, True)[:5] == bytes([0x33, 0x30, 0x01, 0x01, 0x00])
            and build_zone_power(0, False)[:4] == bytes([0x33, 0x30, 0x00, 0x00])
            and build_zone_power(2, True)[2:4] == bytes([0x02, 0x01])
            and build_zone_power(0, True)[19] == 0x02
        ),
    ),
    _check(
        "read_light_direction",
        lambda: bool(read_light_direction() == bytes([0xAA, 0x30] + [0] * 17 + [0xAA ^ 0x30])),
    ),
    _check(
        "read_light_direction",
        lambda: bool(
            read_light_direction() == append_bcc(bytes([0xAA, 0x30]))
            and read_light_direction()[2:19] == bytes(17)
        ),
    ),
    _check(
        "read_op30",
        lambda: bool(read_op30()[:3] == bytes([0xAA, 0x30, 0x00]) and read_op30()[19] == 0xAA ^ 0x30),
    ),
    _check(
        "build_cube_shape_layout",
        lambda: bool(
            (
                lambda f: (
                    len(f) == 2
                    and f[0]
                    == append_bcc(
                        bytes(
                            [
                                0xA3,
                                0x00,
                                0x01,
                                0x02,
                                0x31,
                                0x02,
                                0x04,
                                0x0B,
                                0x00,
                                0xFE,
                                0xFF,
                                0x3C,
                                0x00,
                                0x04,
                                0x00,
                                0x00,
                                0x05,
                                0x00,
                                0x88,
                            ]
                        )
                    )
                    and f[1] == append_bcc(bytes([0xA3, 0xFF, 0xFF, 0x01, 0x02]))
                )
            )(build_cube_shape_layout([(4, 10.2, -2.5, 60), (4, 0.0, 5.0, -120)], ext=(1, 2)))
        ),
    ),
    _check(
        "build_cube_shape_layout_h606a",
        lambda: bool(
            build_cube_shape_layout_h606a(-30, [(10.4, -2.5), (0.5, 3.0)])
            == [
                append_bcc(
                    bytes(
                        [
                            0xA3,
                            0x00,
                            0x01,
                            0x02,
                            0x31,
                            0xE2,
                            0xFF,
                            0x02,
                            0x0A,
                            0x00,
                            0xFE,
                            0xFF,
                            0x01,
                            0x00,
                            0x03,
                            0x00,
                        ]
                    )
                ),
                append_bcc(bytes([0xA3, 0xFF])),
            ]
            and build_cube_shape_layout_h606a(0, [(10.4, -2.5)], use_round=False)[0][8:12]
            == bytes([0x0B, 0x00, 0xFE, 0xFF])
        ),
    ),
    _check(
        "build_fanlight_fan_gear",
        lambda: bool(
            build_fanlight_fan_gear(4)[:5] == bytes([0x33, 0x31, 0x01, 0x04, 0x00])
            and build_fanlight_fan_gear(4)[19] == 0x07
            and build_fanlight_fan_gear(6) == build_fanlight_fan_param(6, 1)[0]
        ),
    ),
    _check(
        "build_fanlight_fan_param",
        lambda: bool(
            [f[:4] for f in build_fanlight_fan_param(6, 1)]
            == [bytes([0x33, 0x31, 0x01, 0x06]), bytes([0x33, 0x31, 0x02, 0x01])]
            and [f[:6] for f in build_fanlight_fan_param(3, 0, 1, 2)][2:]
            == [bytes([0x33, 0x31, 0x04, 0x01, 0x00, 0x00]), bytes([0x33, 0x31, 0x03, 0x01, 0x00, 0x02])]
            and len(build_fanlight_fan_param(3, 0, 1, 2)) == 4
            and all(len(f) == 20 for f in build_fanlight_fan_param(3, 0, 1, 2))
        ),
    ),
    _check(
        "build_fanlight_fan_switch",
        lambda: bool(
            build_fanlight_fan_switch(True)[:5] == bytes([0x33, 0x31, 0x00, 0x01, 0x00])
            and build_fanlight_fan_switch(False)[:4] == bytes([0x33, 0x31, 0x00, 0x00])
            and build_fanlight_fan_switch(False)[19] == 0x02
        ),
    ),
    _check(
        "build_h6057_play_voice",
        lambda: bool(
            build_h6057_play_voice(3)[:4] == bytes([0x33, 0x31, 0x03, 0x00])
            and build_h6057_play_voice(3)[19] == 0x33 ^ 0x31 ^ 0x03
        ),
    ),
    _check(
        "build_h6067_shape_layout",
        lambda: bool(
            build_h6067_shape_layout([(3, 10.2, -2.5, 60, 1), (3, 0.0, 5.0, -120, 2)], 121)
            == [
                append_bcc(
                    bytes(
                        [
                            0xA3,
                            0x00,
                            0x01,
                            0x02,
                            0x31,
                            0x02,
                            0x03,
                            0x0B,
                            0x00,
                            0xFE,
                            0xFF,
                            0x3C,
                            0x00,
                            0x03,
                            0x00,
                            0x00,
                            0x05,
                            0x00,
                            0x88,
                        ]
                    )
                ),
                append_bcc(bytes([0xA3, 0xFF, 0xFF, 0x00, 0x00, 0x01, 0x02])),
            ]
            and build_h6067_shape_layout([(3, 10.2, -2.5, 60, 1), (3, 0.0, 5.0, -120, 2)], 97)
            == [
                append_bcc(
                    bytes(
                        [
                            0xA3,
                            0x00,
                            0x01,
                            0x02,
                            0x31,
                            0x02,
                            0x03,
                            0x0B,
                            0x00,
                            0xFE,
                            0xFF,
                            0x3C,
                            0x00,
                            0x03,
                            0x00,
                            0x00,
                            0x05,
                            0x00,
                            0x88,
                        ]
                    )
                ),
                append_bcc(bytes([0xA3, 0xFF, 0xFF])),
            ]
            and build_h6067_shape_layout([(3, 0.0, 0.0, 0, 7)], 121, ext=(2, 0))[0][5:18]
            == bytes([0x01, 0x03, 0, 0, 0, 0, 0, 0, 0x02, 0x00, 0x00, 0x00, 0x07])
        ),
    ),
    _check(
        "build_op31_byte",
        lambda: bool(
            build_op31_byte(1)[:4] == bytes([0x33, 0x31, 0x01, 0x00]) and build_op31_byte(0)[19] == 0x02
        ),
    ),
    _check(
        "build_strip_light_pos",
        lambda: bool(
            build_strip_light_pos(1)[:4] == bytes([0x33, 0x31, 0x01, 0x00])
            and build_strip_light_pos(0)[19] == 0x02
            and build_strip_light_pos(1)[19] == 0x03
        ),
    ),
    _check(
        "read_op31",
        lambda: bool(read_op31()[:3] == bytes([0xAA, 0x31, 0x00]) and read_op31()[19] == 0xAA ^ 0x31),
    ),
    _check(
        "read_strip_light_pos",
        lambda: bool(read_strip_light_pos() == bytes([0xAA, 0x31] + [0] * 17 + [0x9B])),
    ),
    _check(
        "build_h6057_pause_voice",
        lambda: bool(
            build_h6057_pause_voice() == append_bcc(bytes([0x33, 0x32]))
            and build_h6057_pause_voice()[2:19] == bytes(17)
            and build_h6057_pause_voice()[19] == 0x01
        ),
    ),
    _check(
        "build_h6057_pause_voice",
        lambda: bool(
            build_h6057_pause_voice()[:3] == bytes([0x33, 0x32, 0x00])
            and build_h6057_pause_voice()[19] == 0x33 ^ 0x32
        ),
    ),
    _check(
        "build_pause_voice_h6057",
        lambda: bool(
            build_pause_voice_h6057() == append_bcc(bytes([0x33, 0x32]))
            and build_pause_voice_h6057()[2:19] == bytes(17)
        ),
    ),
    _check(
        "build_pause_voice_h6057_v1",
        lambda: bool(build_pause_voice_h6057_v1(5)[:4] == bytes([0x33, 0x32, 0x05, 0x00])),
    ),
    _check(
        "read_check_camera",
        lambda: bool(
            read_check_camera()[:3] == bytes([0xAA, 0x32, 0x00]) and read_check_camera()[19] == 0xAA ^ 0x32
        ),
    ),
    _check(
        "read_check_camera",
        lambda: bool(
            read_check_camera() == append_bcc(bytes([0xAA, 0x32])) and read_check_camera()[2:19] == bytes(17)
        ),
    ),
    _check(
        "build_lr_switch",
        lambda: bool(
            build_lr_switch(True, True)[:4] == bytes([0x33, 0x33, 0x11, 0x00])
            and build_lr_switch(True, False)[2] == 0x01
            and build_lr_switch(False, True)[2] == 0x10
            and build_lr_switch(False, False)[2] == 0x00
        ),
    ),
    _check(
        "build_volume",
        lambda: bool(
            build_volume(80)[:4] == bytes([0x33, 0x33, 80, 0x00])
            and build_volume(0)[:3] == bytes([0x33, 0x33, 0x00])
        ),
    ),
    _check(
        "build_volume_h6057",
        lambda: bool(
            build_volume_h6057(80)[:4] == bytes([0x33, 0x33, 80, 0x00])
            and build_volume_h6057(0) == bytes([0x33, 0x33]) + bytes(18)
        ),
    ),
    _check(
        "build_volume_h6057",
        lambda: bool(
            build_volume_h6057(80)[:4] == bytes([0x33, 0x33, 80, 0x00])
            and build_volume_h6057(0) == bytes([0x33, 0x33]) + bytes(18)
        ),
    ),
    _check(
        "read_lr_switch",
        lambda: bool(
            read_lr_switch()[:3] == bytes([0xAA, 0x33, 0x00]) and read_lr_switch()[19] == (0xAA ^ 0x33)
        ),
    ),
    _check(
        "build_swap_light",
        lambda: bool(build_swap_light() == bytes([0x33, 0x34]) + bytes(17) + bytes([0x33 ^ 0x34])),
    ),
    _check(
        "read_h6057_detail",
        lambda: bool(read_h6057_detail() == bytes([0xAA, 0x34]) + bytes(17) + bytes([0xAA ^ 0x34])),
    ),
    _check(
        "build_without_interrupt",
        lambda: bool(
            build_without_interrupt(True) == bytes.fromhex("3335010000000000000000000000000000000007")
        ),
    ),
    _check(
        "build_without_interrupt",
        lambda: bool(
            build_without_interrupt(True)[:4] == bytes([0x33, 0x35, 0x01, 0x00])
            and build_without_interrupt(False)[:3] == bytes([0x33, 0x35, 0x00])
        ),
    ),
    _check(
        "build_without_interrupt",
        lambda: bool(
            build_without_interrupt(True) == bytes.fromhex("3335010000000000000000000000000000000007")
            and build_without_interrupt(False)[:3] == bytes([0x33, 0x35, 0x00])
        ),
    ),
    _check(
        "build_bar_switch_index",
        lambda: bool(
            build_bar_switch_index(1, True)[:5] == bytes([0x33, 0x36, 0x01, 0x01, 0x00])
            and build_bar_switch_index(0, False)[:4] == bytes([0x33, 0x36, 0x00, 0x00])
            and build_bar_switch_index(2, True)[:4] == bytes([0x33, 0x36, 0x02, 0x01])
        ),
    ),
    _check(
        "build_bar_switch_index",
        lambda: bool(
            [build_bar_switch_index(1, True)[:4], build_bar_switch_index(0, False)[:4]]
            == [bytes([0x33, 0x36, 0x01, 0x01]), bytes([0x33, 0x36, 0x00, 0x00])]
            and build_bar_switch_index(2, True)[:5] == bytes([0x33, 0x36, 0x02, 0x01, 0x00])
            and build_bar_switch_index(1, True)[19] == (0x33 ^ 0x36 ^ 0x01 ^ 0x01)
        ),
    ),
    _check(
        "build_bar_switch_pair",
        lambda: bool(
            build_bar_switch_pair(True, False)[:5] == bytes([0x33, 0x36, 0x01, 0x00, 0x00])
            and build_bar_switch_pair(True, True)[:4] == bytes([0x33, 0x36, 0x01, 0x01])
            and build_bar_switch_pair(False, True)[:4] == bytes([0x33, 0x36, 0x00, 0x01])
        ),
    ),
    _check(
        "build_child_switch",
        lambda: bool(
            build_child_switch(True, True) == append_bcc(bytes([0x33, 0x36, 0x00, 0x01]))
            and build_child_switch(False, False)[:4] == bytes([0x33, 0x36, 0x01, 0x00])
            and build_child_switch(False, True)[19] == 0x05
            and build_child_switch(True, False) == bytes.fromhex("3336000000000000000000000000000000000005")
        ),
    ),
    _check(
        "build_child_switch",
        lambda: bool(
            build_child_switch(True, True)[:5] == bytes([0x33, 0x36, 0x00, 0x01, 0x00])
            and build_child_switch(False, True)[:4] == bytes([0x33, 0x36, 0x01, 0x01])
            and build_child_switch(True, False)[:4] == bytes([0x33, 0x36, 0x00, 0x00])
        ),
    ),
    _check(
        "build_prompt_tone",
        lambda: bool(build_prompt_tone(True) == bytes.fromhex("3337010000000000000000000000000000000005")),
    ),
    _check(
        "build_prompt_tone",
        lambda: bool(
            build_prompt_tone(True)[:4] == bytes([0x33, 0x37, 0x01, 0x00])
            and build_prompt_tone(False)[:3] == bytes([0x33, 0x37, 0x00])
        ),
    ),
    _check(
        "build_prompt_tone",
        lambda: bool(
            build_prompt_tone(True) == bytes.fromhex("3337010000000000000000000000000000000005")
            and build_prompt_tone(False)[:3] == bytes([0x33, 0x37, 0x00])
        ),
    ),
    _check(
        "build_guide_light_h6057",
        lambda: bool(
            build_guide_light_h6057(True) == bytes.fromhex("333801000000000000000000000000000000000a")
            and build_guide_light_h6057(False)[:3] == bytes([0x33, 0x38, 0x00])
        ),
    ),
    _check(
        "build_guide_light_h6057",
        lambda: bool(
            build_guide_light_h6057(True)[:4] == bytes([0x33, 0x38, 0x01, 0x00])
            and build_guide_light_h6057(False)[:3] == bytes([0x33, 0x38, 0x00])
        ),
    ),
    _check(
        "build_guide_light_h6057",
        lambda: bool(
            build_guide_light_h6057(True) == bytes.fromhex("333801000000000000000000000000000000000a")
            and build_guide_light_h6057(False)[:3] == bytes([0x33, 0x38, 0x00])
        ),
    ),
    _check(
        "build_init_light",
        lambda: bool(
            build_init_light(1) == bytes([0x33, 0x38, 0x01] + [0] * 16 + [0x33 ^ 0x38 ^ 0x01])
            and build_init_light(0)[:3] == bytes([0x33, 0x38, 0x00])
        ),
    ),
    _check(
        "build_check_direction",
        lambda: bool(
            build_check_direction(2)[:4] == bytes([0x33, 0x39, 0x02, 0x00])
            and build_check_direction(2)[19] == 0x33 ^ 0x39 ^ 0x02
        ),
    ),
    _check(
        "read_check_direction",
        lambda: bool(
            read_check_direction()[:3] == bytes([0xAA, 0x39, 0x00])
            and read_check_direction()[19] == 0xAA ^ 0x39
        ),
    ),
    _check(
        "build_splicing_config",
        lambda: bool(
            build_splicing_config(2, 0, ["AA:BB:CC:DD:EE:FF"])
            == bytes.fromhex("334001026cffeeddccbbaa00000000000000000d")
        ),
    ),
    _check(
        "build_splicing_mode",
        lambda: bool(
            build_splicing_mode((255, 0, 0)) == bytes.fromhex("33400201ff00000000000000000000000000008f")
            and build_splicing_mode() == bytes.fromhex("3340020000000000000000000000000000000071")
        ),
    ),
    _check(
        "build_splicing_release",
        lambda: bool(build_splicing_release() == bytes.fromhex("334001ff0000000000000000000000000000008d")),
    ),
    _check(
        "build_splicing_sub_device_mac",
        lambda: bool(
            build_splicing_sub_device_mac(1, "AA:BB:CC:DD:EE:FF")
            == bytes.fromhex("33400501ffeeddccbbaa00000000000000000066")
        ),
    ),
    _check(
        "read_status_with_ic",
        lambda: bool(
            read_status_with_ic() == bytes.fromhex("ac03034041a50000000000000000000000000008")
            and read_status_with_ic((0x40, 0xA5))[:5] == bytes([0xAC, 0x03, 0x02, 0x40, 0xA5])
        ),
    ),
    _check(
        "build_music_effect_a3_v2",
        lambda: bool(
            build_music_effect_a3_v2(50, [(255, 0, 0), (0, 0, 255)], (3, 1, 50))
            == [
                bytes.fromhex("a3000102413202ff00000000ff030132000000e1"),
                bytes.fromhex("a3ff00000000000000000000000000000000005c"),
            ]
            and build_music_effect_a3_v2(
                51,
                [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0)],
                params=(0, 0, 255, 100, 50, 1, 5, 1, 8),
            )[1]
            == bytes.fromhex("a3ff0000ff6432010501080000000000000000f8")
            and build_music_effect_a3_v2(69, [(255, 0, 0)], pad=3)[0]
            == bytes.fromhex("a3000102414501ff00000000000000000000005a")
            and build_music_effect_a3_v2(0x32, [(255, 0, 0)], params=(2, 7))
            == [
                bytes.fromhex("a3000102413201ff000002070000000000000028"),
                bytes.fromhex("a3ff00000000000000000000000000000000005c"),
            ]
            and build_music_effect_a3_v2(0x80, [(1, 2, 3)], head=(1, 2, 3, 0))[0]
            == bytes.fromhex("a300010241800102030001010203000000000060")
            and build_music_effect_a3_v2(0x91, [(0, 255, 0), (0, 0, 255)], head=(1, 4))[0]
            == bytes.fromhex("a3000102419101040200ff000000ff0000000077")
        ),
    ),
    _check(
        "build_on_off_memory",
        lambda: bool(
            build_on_off_memory(1) == bytes([0x33, 0x41, 0x02, 0x01] + [0] * 15 + [0x33 ^ 0x41 ^ 0x02 ^ 0x01])
        ),
    ),
    _check(
        "build_on_off_memory",
        lambda: bool(
            build_on_off_memory(1)[:5] == bytes([0x33, 0x41, 0x02, 0x01, 0x00])
            and build_on_off_memory(2)[19] == (0x33 ^ 0x41 ^ 0x02 ^ 0x02)
        ),
    ),
    _check(
        "build_on_off_memory",
        lambda: bool(
            build_on_off_memory(1)[:5] == bytes([0x33, 0x41, 0x02, 0x01, 0x00])
            and build_on_off_memory(2)[19] == (0x33 ^ 0x41 ^ 0x02 ^ 0x02)
        ),
    ),
    _check(
        "build_on_off_memory_legacy",
        lambda: bool(
            build_on_off_memory_legacy(True) == bytes([0x33, 0x41, 0x01] + [0] * 16 + [0x33 ^ 0x41 ^ 0x01])
            and build_on_off_memory_legacy(False)[:3] == bytes([0x33, 0x41, 0x00])
        ),
    ),
    _check(
        "build_on_off_memory_legacy",
        lambda: bool(
            build_on_off_memory_legacy(True)[:4] == bytes([0x33, 0x41, 0x01, 0x00])
            and build_on_off_memory_legacy(False)[:3] == bytes([0x33, 0x41, 0x00])
        ),
    ),
    _check(
        "read_on_off_memory",
        lambda: bool(read_on_off_memory() == bytes([0xAA, 0x41, 0x02] + [0] * 16 + [0xAA ^ 0x41 ^ 0x02])),
    ),
    _check(
        "read_on_off_memory",
        lambda: bool(read_on_off_memory() == bytes([0xAA, 0x41, 0x02] + [0] * 16 + [0xAA ^ 0x41 ^ 0x02])),
    ),
    _check(
        "read_on_off_memory",
        lambda: bool(
            read_on_off_memory()[:4] == bytes([0xAA, 0x41, 0x02, 0x00])
            and read_on_off_memory()[19] == (0xAA ^ 0x41 ^ 0x02)
        ),
    ),
    _check(
        "read_on_off_memory_or_section_count",
        lambda: bool(read_on_off_memory_or_section_count() == bytes([0xAA, 0x41] + [0] * 17 + [0xEB])),
    ),
    _check(
        "read_on_off_memory_or_section_count",
        lambda: bool(
            read_on_off_memory_or_section_count()[:3] == bytes([0xAA, 0x41, 0x00])
            and read_on_off_memory_or_section_count()[19] == 0xEB
        ),
    ),
    _check(
        "build_head_calibration",
        lambda: bool(
            build_head_calibration(2, 1) == bytes.fromhex("3342020100000000000000000000000000000072")
            and build_head_calibration(0xFF) == bytes.fromhex("3342ff000000000000000000000000000000008e")
        ),
    ),
    _check(
        "build_head_calibration",
        lambda: bool(
            build_head_calibration(0x01) == bytes.fromhex("3342010000000000000000000000000000000070")
            and build_head_calibration(2, 1) == bytes.fromhex("3342020100000000000000000000000000000072")
            and build_head_calibration(2, 2) == bytes.fromhex("3342020200000000000000000000000000000071")
            and [build_head_calibration(0xFF), build_head_calibration(0x00)]
            == [
                bytes.fromhex("3342ff000000000000000000000000000000008e"),
                bytes.fromhex("3342000000000000000000000000000000000071"),
            ]
        ),
    ),
    _check(
        "build_head_calibration_enter",
        lambda: bool(
            build_head_calibration_enter() == bytes.fromhex("3342010000000000000000000000000000000070")
        ),
    ),
    _check(
        "build_head_calibration_exit",
        lambda: bool(
            build_head_calibration_exit() == bytes.fromhex("3342000000000000000000000000000000000071")
        ),
    ),
    _check(
        "build_head_calibration_finish",
        lambda: bool(
            build_head_calibration_finish()
            == [
                bytes.fromhex("3342ff000000000000000000000000000000008e"),
                bytes.fromhex("3342000000000000000000000000000000000071"),
            ]
        ),
    ),
    _check(
        "build_head_calibration_step",
        lambda: bool(
            build_head_calibration_step(True) == bytes.fromhex("3342020100000000000000000000000000000072")
            and build_head_calibration_step(False)
            == bytes.fromhex("3342020200000000000000000000000000000071")
        ),
    ),
    _check(
        "build_refresh_ic",
        lambda: bool(build_refresh_ic() == bytes.fromhex("3342000000000000000000000000000000000071")),
    ),
    _check(
        "build_splicing_cancel_bt",
        lambda: bool(build_splicing_cancel_bt() == bytes.fromhex("33420b000000000000000000000000000000007a")),
    ),
    _check(
        "build_splicing_cancel_bt",
        lambda: bool(build_splicing_cancel_bt() == bytes.fromhex("33420b000000000000000000000000000000007a")),
    ),
    _check(
        "build_spp_mac_write",
        lambda: bool(
            build_spp_mac_write("AA:BB:CC:DD:EE:FF")
            == bytes.fromhex("33420cffeeddccbbaa000000000000000000006c")
            and build_spp_mac_write("A4:C1:38:01:02:03")[:9]
            == bytes([0x33, 0x42, 0x0C, 0x03, 0x02, 0x01, 0x38, 0xC1, 0xA4])
        ),
    ),
    _check(
        "build_spp_mac_write",
        lambda: bool(
            build_spp_mac_write("AA:BB:CC:DD:EE:FF")
            == bytes.fromhex("33420cffeeddccbbaa000000000000000000006c")
        ),
    ),
    _check(
        "read_spp_mac",
        lambda: bool(read_spp_mac() == bytes.fromhex("aa420600000000000000000000000000000000ee")),
    ),
    _check(
        "read_spp_mac",
        lambda: bool(read_spp_mac() == bytes.fromhex("aa420600000000000000000000000000000000ee")),
    ),
    _check(
        "build_check_ic_amount",
        lambda: bool(
            build_check_ic_amount(2, 300) == bytes.fromhex("334302012c00000000000000000000000000005f")
            and build_check_ic_amount(4, 300) == bytes.fromhex("334304012c000000000000000000000000000059")
            and build_check_ic_amount(0) == bytes.fromhex("3343000000000000000000000000000000000070")
        ),
    ),
    _check(
        "build_check_ic_h6640",
        lambda: bool(
            build_check_ic_h6640(4, 300)[:6] == bytes([0x33, 0x43, 0x04, 0x01, 0x2C, 0x00])
            and build_check_ic_h6640(1, 300) == append_bcc(bytes([0x33, 0x43, 0x01]))
            and build_check_ic_h6640(3)[19] == (0x33 ^ 0x43 ^ 0x03)
        ),
    ),
    _check(
        "build_check_ic_h6640",
        lambda: bool(
            build_check_ic_h6640(3)[:4] == bytes([0x33, 0x43, 0x03, 0x00])
            and build_check_ic_h6640(4, 0x0123)[:6] == bytes([0x33, 0x43, 0x04, 0x01, 0x23, 0x00])
        ),
    ),
    _check(
        "read_sub_device_version",
        lambda: bool(read_sub_device_version() == bytes.fromhex("aa430000000000000000000000000000000000e9")),
    ),
    _check(
        "build_calibration",
        lambda: bool(
            build_calibration(True) == bytes.fromhex("3344010000000000000000000000000000000076")
            and build_calibration(False) == bytes.fromhex("3344000000000000000000000000000000000077")
        ),
    ),
    _check(
        "build_calibration",
        lambda: bool(
            build_calibration(True) == bytes.fromhex("3344010000000000000000000000000000000076")
            and build_calibration(False)
            == append_bcc(bytes([0x33, 0x44]))
            == bytes([0x33, 0x44] + [0] * 17 + [0x77])
        ),
    ),
    _check(
        "build_calibration_done_h6048",
        lambda: bool(
            build_calibration_done_h6048() == append_bcc(bytes([0x33, 0x44]))
            and build_calibration_done_h6048()[2:19] == bytes(17)
        ),
    ),
    _check(
        "build_calibration_mode",
        lambda: bool(
            build_calibration_mode(True) == bytes.fromhex("3344020100000000000000000000000000000074")
        ),
    ),
    _check(
        "build_calibration_onoff",
        lambda: bool(
            build_calibration_onoff(True)[:4] == bytes([0x33, 0x44, 0x01, 0x00])
            and build_calibration_onoff(False)[:3] == bytes([0x33, 0x44, 0x00])
            and build_calibration_onoff(False)[2:19] == bytes(17)
        ),
    ),
    _check(
        "build_cube_calibration",
        lambda: bool(
            build_cube_calibration(255, 4) == bytes.fromhex("3344ff040000000000000000000000000000008c")
        ),
    ),
    _check(
        "build_h6065_calibration",
        lambda: bool(
            build_h6065_calibration([1, 2, 3]) == bytes.fromhex("33440f0102030000000000000000000000000078")
        ),
    ),
    _check(
        "build_h6067_calibration",
        lambda: bool(build_h6067_calibration(3) == bytes.fromhex("3344030000000000000000000000000000000074")),
    ),
    _check(
        "read_splicing_status",
        lambda: bool(read_splicing_status() == bytes.fromhex("aa440000000000000000000000000000000000ee")),
    ),
    _check(
        "build_cube_install",
        lambda: bool(
            build_cube_install(True)[:4] == bytes([0x33, 0x45, 0x00, 0x00])
            and build_cube_install(False)[:3] == bytes([0x33, 0x45, 0xFF])
            and build_cube_install(False)[19] == 0x33 ^ 0x45 ^ 0xFF
        ),
    ),
    _check(
        "build_install_mode",
        lambda: bool(
            build_install_mode(True) == bytes([0x33, 0x45, 0x00] + [0] * 16 + [0x33 ^ 0x45])
            and build_install_mode(False)[:3] == bytes([0x33, 0x45, 0xFF])
        ),
    ),
    _check(
        "build_install_mode",
        lambda: bool(
            build_install_mode(True) == bytes([0x33, 0x45, 0x00] + [0] * 16 + [0x33 ^ 0x45])
            and build_install_mode(False)[:3] == bytes([0x33, 0x45, 0xFF])
            and build_install_mode(False)[19] == (0x33 ^ 0x45 ^ 0xFF)
        ),
    ),
    _check(
        "build_montage_brightness",
        lambda: bool(
            build_montage_brightness(100) == bytes([0x33, 0x45, 0x64] + [0] * 16 + [0x33 ^ 0x45 ^ 0x64])
        ),
    ),
    _check(
        "build_montage_brightness",
        lambda: bool(
            build_montage_brightness(80)[:4] == bytes([0x33, 0x45, 80, 0x00])
            and build_montage_brightness(80)[19] == (0x33 ^ 0x45 ^ 80)
        ),
    ),
    _check(
        "read_montage_brightness",
        lambda: bool(read_montage_brightness() == bytes([0xAA, 0x45] + [0] * 17 + [0xAA ^ 0x45])),
    ),
    _check(
        "read_montage_brightness",
        lambda: bool(read_montage_brightness() == bytes([0xAA, 0x45] + [0] * 17 + [0xAA ^ 0x45])),
    ),
    _check(
        "build_check_ic_step",
        lambda: bool(
            build_check_ic_step(0) == bytes.fromhex("3346000000000000000000000000000000000075")
            and build_check_ic_step(3) == bytes.fromhex("3346030000000000000000000000000000000076")
            and build_check_ic_step(4) == bytes.fromhex("3346040000000000000000000000000000000071")
            and build_check_ic_step(4, cmd=0x5A) == bytes.fromhex("335a04000000000000000000000000000000006d")
        ),
    ),
    _check(
        "build_check_ic_step",
        lambda: bool(
            build_check_ic_step(0)[:4] == bytes([0x33, 0x46, 0x00, 0x00])
            and build_check_ic_step(4)[:3] == bytes([0x33, 0x46, 0x04])
            and build_check_ic_step(3)[3:19] == bytes(16)
        ),
    ),
    _check(
        "build_reset_device_net",
        lambda: bool(build_reset_device_net() == bytes.fromhex("3346000000000000000000000000000000000075")),
    ),
    _check(
        "build_reset_device_net",
        lambda: bool(build_reset_device_net() == bytes.fromhex("3346000000000000000000000000000000000075")),
    ),
    _check(
        "build_splicing_change_package",
        lambda: bool(
            build_splicing_change_package(True) == bytes.fromhex("3346010000000000000000000000000000000074")
        ),
    ),
    _check(
        "build_strip_len_cali_crop",
        lambda: bool(
            build_strip_len_cali_crop([40, 40], True, cmd=0x5A)
            == bytes.fromhex("335a06020028002800000000000000000000006d")
        ),
    ),
    _check(
        "build_part_scenes_47",
        lambda: bool(
            build_part_scenes_47(0x0102, [0, 2, 7])[:11]
            == bytes([0x33, 0x05, 0x47, 0x02, 0x01, 0x01, 0x00, 0x01, 0x00, 0x00, 0x00])
        ),
    ),
    _check(
        "build_part_scenes_47",
        lambda: bool(
            build_part_scenes_47(0x0102, [0, 2, 7])[:11]
            == bytes([0x33, 0x05, 0x47, 0x02, 0x01, 0x01, 0x00, 0x01, 0x00, 0x00, 0x00])
            and build_part_scenes_47(0)[:10] == bytes([0x33, 0x05, 0x47, 0, 0, 0, 0, 0, 0, 0])
        ),
    ),
    _check(
        "build_part_scenes_info",
        lambda: bool(
            build_part_scenes_info(b"\x01\x02")[:5] == bytes([0x33, 0x48, 1, 2, 0])
            and build_part_scenes_info(b"\x01\x02")[19] == (0x33 ^ 0x48 ^ 1 ^ 2)
            and build_part_scenes_info(bytes(range(1, 21)))[2:19] == bytes(range(1, 18))
        ),
    ),
    _check(
        "read_part_scenes_info",
        lambda: bool(read_part_scenes_info() == bytes([0xAA, 0x48] + [0] * 17 + [0xAA ^ 0x48])),
    ),
    _check(
        "read_part_scenes_info",
        lambda: bool(read_part_scenes_info() == bytes([0xAA, 0x48] + [0] * 17 + [0xAA ^ 0x48])),
    ),
    _check(
        "read_wifi_hard_version_v2",
        lambda: bool(
            read_wifi_hard_version_v2() == bytes([0xAA, 0x49, 0x03] + [0] * 16 + [0xAA ^ 0x49 ^ 0x03])
        ),
    ),
    _check(
        "read_wifi_info_v2",
        lambda: bool(
            read_wifi_info_v2(1) == bytes([0xAA, 0x49, 0x01] + [0] * 16 + [0xAA ^ 0x49 ^ 0x01])
            and read_wifi_info_v2(3)[:3] == bytes([0xAA, 0x49, 0x03])
        ),
    ),
    _check(
        "read_wifi_info_v2",
        lambda: bool(
            read_wifi_info_v2(1) == bytes([0xAA, 0x49, 0x01] + [0] * 16 + [0xAA ^ 0x49 ^ 0x01])
            and read_wifi_info_v2(2)[:3] == bytes([0xAA, 0x49, 0x02])
            and read_wifi_info_v2(3)[:3] == bytes([0xAA, 0x49, 0x03])
        ),
    ),
    _check(
        "read_wifi_mac_v2",
        lambda: bool(read_wifi_mac_v2() == bytes([0xAA, 0x49, 0x01] + [0] * 16 + [0xAA ^ 0x49 ^ 0x01])),
    ),
    _check(
        "read_wifi_soft_version_v2",
        lambda: bool(
            read_wifi_soft_version_v2() == bytes([0xAA, 0x49, 0x02] + [0] * 16 + [0xAA ^ 0x49 ^ 0x02])
        ),
    ),
    _check(
        "build_check_network",
        lambda: bool(build_check_network() == bytes([0x33, 0x4A, 0xFF] + [0] * 16 + [0x33 ^ 0x4A ^ 0xFF])),
    ),
    _check(
        "build_h6069_set_device_recognition",
        lambda: bool(
            build_h6069_set_device_recognition([3, 1, 2])
            == bytes([0x33, 0x50, 0x03, 0x03, 0x01, 0x02] + [0x00] * 13 + [0x60])
            and build_h6069_set_device_recognition(list(range(30)))[2] == 30
            and build_h6069_set_device_recognition(list(range(30)))[18] == 15
        ),
    ),
    _check(
        "build_movie_feast_set_sub_device",
        lambda: bool(
            (
                lambda f: (
                    len(f) == 3
                    and f[0][:5] == bytes([0xA3, 0x00, 0x01, 0x03, 0x50])
                    and f[0][5:19] == bytes(range(14))
                    and f[1][:3] == bytes([0xA3, 0x01, 0x0E])
                    and f[2][:4] == bytes([0xA3, 0xFF, 0x1F, 0x20])
                    and f[2][11:19] == bytes(8)
                )
            )(build_movie_feast_set_sub_device(bytes(range(40))))
        ),
    ),
    _check(
        "build_cube_calibration_all_h6066",
        lambda: bool(
            build_cube_calibration_all_h6066([4, 0, 2])
            == [
                append_bcc(bytes([0xA3, 0x00, 0x01, 0x02, 0x52, 0x04, 0x03, 0x04, 0x06, 0x0E])),
                append_bcc(bytes([0xA3, 0xFF])),
            ]
            and len(build_cube_calibration_all_h6066([0] * 12)) == 2
            and len(build_cube_calibration_all_h6066([0] * 13)) == 2
            and build_cube_calibration_all_h6066([0] * 13)[0][3] == 0x02
            and build_cube_calibration_all_h6066([0] * 13)[1][:3] == bytes([0xA3, 0xFF, 0x48])
            and len(build_cube_calibration_all_h6066([0] * 30)) == 3
            and build_cube_calibration_all_h6066([0] * 30)[1][:2] == bytes([0xA3, 0x01])
        ),
    ),
    _check(
        "build_h6067_calibration_add_not",
        lambda: bool(
            build_h6067_calibration_add_not(15, [3, 4], 2, [7, 8, 9])
            == [
                append_bcc(
                    bytes(
                        [0xA3, 0x00, 0x01, 0x02, 0x52, 0x0F, 0x03, 0x04, 0x01, 0x0F, 0x02, 0x07, 0x08, 0x09]
                    )
                ),
                append_bcc(bytes([0xA3, 0xFF])),
            ]
        ),
    ),
    _check(
        "build_h6067_calibration_all",
        lambda: bool(
            build_h6067_calibration_all(15, 2, [3, 4], [1, 2, 3, 4])[0]
            == append_bcc(
                bytes(
                    [0xA3, 0x00, 0x01, 0x02, 0x52, 0x0F, 0x03, 0x04, 0x01, 0x0F, 0x01, 0x01, 0x02, 0x03, 0x04]
                )
            )
            and build_h6067_calibration_all(15, 0, [3, 4], [3, 4])[0][5:13]
            == bytes([0x0F, 0x03, 0x04, 0x01, 0x0F, 0x00, 0x00, 0x00])
            and build_h6067_calibration_all(15, 0, [3, 4], [3, 4])[1] == append_bcc(bytes([0xA3, 0xFF]))
        ),
    ),
    _check(
        "build_movie_feast_on_off",
        lambda: bool(
            build_movie_feast_on_off(True) == bytes([0x33, 0x54, 0x01] + [0x00] * 16 + [0x66])
            and build_movie_feast_on_off(False) == bytes([0x33, 0x54, 0x00] + [0x00] * 16 + [0x67])
        ),
    ),
    _check(
        "read_movie_feast_on_off",
        lambda: bool(read_movie_feast_on_off() == bytes([0xAA, 0x54] + [0x00] * 17 + [0xFE])),
    ),
    _check(
        "read_movie_feast_on_off",
        lambda: bool(
            read_movie_feast_on_off()[:3] == bytes([0xAA, 0x54, 0x00])
            and read_movie_feast_on_off()[19] == 0xFE
        ),
    ),
    _check(
        "build_carousel_frames",
        lambda: bool(
            build_carousel_frames(1, [bytes([0x00, 0x10, 0x27])])
            == [
                bytes.fromhex("a3000102600101001027000000000000000000f7"),
                bytes.fromhex("a3ff00000000000000000000000000000000005c"),
            ]
            and build_carousel_frames(0, [bytes(range(20)), bytes(range(20, 38))])
            == [
                bytes.fromhex("a3000103600002000102030405060708090a0bc3"),
                bytes.fromhex("a3010c0d0e0f101112131415161718191a1b1cbe"),
                bytes.fromhex("a3ff1d1e1f202122232425000000000000000041"),
            ]
        ),
    ),
    _check(
        "build_carousel_frames",
        lambda: bool(
            (
                lambda f: (
                    len(f) == 2
                    and f[0][:10] == bytes([0xA3, 0x00, 0x01, 0x02, 0x60, 0x01, 0x01, 0x00, 0x10, 0x27])
                    and f[1][:2] == bytes([0xA3, 0xFF])
                )
            )(build_carousel_frames(1, [bytes([0x00, 0x10, 0x27])]))
        ),
    ),
    _check(
        "build_carousel_frames",
        lambda: bool(
            (
                lambda f: (
                    len(f) == 2
                    and f[0][:10] == bytes([0xA3, 0x00, 0x01, 0x02, 0x60, 0x01, 0x01, 0x00, 0x10, 0x27])
                    and f[1][:2] == bytes([0xA3, 0xFF])
                )
            )(build_carousel_frames(1, [bytes([0x00, 0x10, 0x27])]))
        ),
    ),
    _check(
        "build_feast_module_movie_on_off",
        lambda: bool(
            build_feast_module_movie_on_off(True) == bytes.fromhex("3360010101000000000000000000000000000052")
            and build_feast_module_movie_on_off(False)
            == bytes.fromhex("3360010001000000000000000000000000000053")
        ),
    ),
    _check(
        "build_feast_module_movie_on_off",
        lambda: bool(
            build_feast_module_movie_on_off(True)
            == bytes([0x33, 0x60, 0x01, 0x01, 0x01] + [0x00] * 14 + [0x52])
            and build_feast_module_movie_on_off(False)[:5] == bytes([0x33, 0x60, 0x01, 0x00, 0x01])
        ),
    ),
    _check(
        "build_feast_module_movie_on_off",
        lambda: bool(
            build_feast_module_movie_on_off(True)[:6] == bytes([0x33, 0x60, 0x01, 0x01, 0x01, 0x00])
            and build_feast_module_movie_on_off(False)[3] == 0x00
            and build_feast_module_movie_on_off(True)[19] == 0x52
        ),
    ),
    _check(
        "build_feast_module_movie_on_off",
        lambda: bool(
            build_feast_module_movie_on_off(False)[:6] == bytes([0x33, 0x60, 0x01, 0x00, 0x01, 0x00])
            and build_feast_module_movie_on_off(True)[:5] == bytes([0x33, 0x60, 0x01, 0x01, 0x01])
            and build_feast_module_movie_on_off(True)[19] == 0x52
        ),
    ),
    _check(
        "build_feast_module_movie_on_off",
        lambda: bool(
            build_feast_module_movie_on_off(True)[:6] == bytes([0x33, 0x60, 0x01, 0x01, 0x01, 0x00])
            and build_feast_module_movie_on_off(False)[3] == 0x00
        ),
    ),
    _check(
        "build_feast_module_switch",
        lambda: bool(
            build_feast_module_switch(True) == bytes.fromhex("3360010100000000000000000000000000000053")
            and build_feast_module_switch(False) == bytes.fromhex("3360010000000000000000000000000000000052")
        ),
    ),
    _check(
        "build_feast_module_switch",
        lambda: bool(
            build_feast_module_switch(True)[:5] == bytes([0x33, 0x60, 0x01, 0x01, 0x00])
            and build_feast_module_switch(True)[19] == 0x53
            and build_feast_module_switch(False)[3] == 0x00
        ),
    ),
    _check(
        "build_feast_module_switch",
        lambda: bool(
            build_feast_module_switch(False)[:5] == bytes([0x33, 0x60, 0x01, 0x00, 0x00])
            and build_feast_module_switch(True)[3] == 0x01
        ),
    ),
    _check(
        "build_feast_module_switch",
        lambda: bool(
            build_feast_module_switch(True) == bytes([0x33, 0x60, 0x01, 0x01] + [0x00] * 15 + [0x53])
            and build_feast_module_switch(False)[:5] == bytes([0x33, 0x60, 0x01, 0x00, 0x00])
        ),
    ),
    _check(
        "read_feast_module_movie_on_off",
        lambda: bool(
            read_feast_module_movie_on_off() == bytes.fromhex("aa600101000000000000000000000000000000ca")
        ),
    ),
    _check(
        "read_feast_module_movie_on_off",
        lambda: bool(
            read_feast_module_movie_on_off() == bytes([0xAA, 0x60, 0x01, 0x01] + [0x00] * 15 + [0xCA])
        ),
    ),
    _check(
        "read_feast_module_movie_on_off",
        lambda: bool(
            read_feast_module_movie_on_off()[:5] == bytes([0xAA, 0x60, 0x01, 0x01, 0x00])
            and read_feast_module_movie_on_off()[19] == 0xCA
        ),
    ),
    _check(
        "read_feast_module_movie_on_off",
        lambda: bool(
            read_feast_module_movie_on_off()[:5] == bytes([0xAA, 0x60, 0x01, 0x01, 0x00])
            and read_feast_module_movie_on_off()[19] == 0xCA
        ),
    ),
    _check(
        "read_feast_module_movie_on_off",
        lambda: bool(
            read_feast_module_movie_on_off()[:5] == bytes([0xAA, 0x60, 0x01, 0x01, 0x00])
            and read_feast_module_movie_on_off()[19] == 0xCA
        ),
    ),
    _check(
        "read_feast_module_switch",
        lambda: bool(read_feast_module_switch() == bytes.fromhex("aa600100000000000000000000000000000000cb")),
    ),
    _check(
        "read_feast_module_switch",
        lambda: bool(
            read_feast_module_switch()[:4] == bytes([0xAA, 0x60, 0x01, 0x00])
            and read_feast_module_switch()[19] == 0xCB
        ),
    ),
    _check(
        "read_feast_module_switch",
        lambda: bool(
            read_feast_module_switch()[:4] == bytes([0xAA, 0x60, 0x01, 0x00])
            and read_feast_module_switch()[19] == 0xCB
        ),
    ),
    _check(
        "read_feast_module_switch",
        lambda: bool(read_feast_module_switch() == bytes([0xAA, 0x60, 0x01] + [0x00] * 16 + [0xCB])),
    ),
    _check(
        "build_combination_effect_apply_mode",
        lambda: bool(
            build_combination_effect_apply_mode(0x1234)
            == bytes([0x33, 0x05, 0x0A, 0x34, 0x12] + [0x00] * 14 + [0x1A])
        ),
    ),
    _check(
        "build_combination_effect_apply_mode",
        lambda: bool(
            build_combination_effect_apply_mode(0x1234)
            == bytes.fromhex("33050a341200000000000000000000000000001a")
        ),
    ),
    _check(
        "build_combination_effect_apply_mode",
        lambda: bool(
            build_combination_effect_apply_mode(0x1234)[:5] == bytes([0x33, 0x05, 0x0A, 0x34, 0x12])
        ),
    ),
    _check(
        "build_combination_effect_frames",
        lambda: bool(
            build_combination_effect_frames(bytes(11))[0][:7]
            == bytes([0xA4, 0x00, 0x00, 0x01, 0x02, 0x00, 0x62])
            and len(build_combination_effect_frames(bytes(11))[0]) == 19
            and build_combination_effect_frames(bytes(11))[1] == bytes([0xA4, 0xFF, 0xFF, 0xA4])
            and len(build_combination_effect_frames(bytes(187))) == 12
            and [len(f) for f in build_combination_effect_frames(bytes(187))][-1] == 19
            and build_combination_effect_frames(bytes(187))[0][4:7] == bytes([0x0C, 0x00, 0x62])
        ),
    ),
    _check(
        "build_combination_effect_frames",
        lambda: bool(
            build_combination_effect_frames(bytes(11))
            == [bytes.fromhex("a40000010200620000000000000000000000c5"), bytes.fromhex("a4ffffa4")]
            and build_combination_effect_frames(bytes(range(30)))
            == [
                bytes.fromhex("a4000001030062000102030405060708090a0bc4"),
                bytes.fromhex("a401000c0d0e0f101112131415161718191a1ba5"),
                bytes.fromhex("a4ffff1c1da5"),
            ]
        ),
    ),
    _check(
        "build_combination_effect_frames",
        lambda: bool(
            build_combination_effect_frames(bytes(11))[0][:7]
            == bytes([0xA4, 0x00, 0x00, 0x01, 0x02, 0x00, 0x62])
            and len(build_combination_effect_frames(bytes(11))[0]) == 19
            and build_combination_effect_frames(bytes(11))[1] == bytes([0xA4, 0xFF, 0xFF, 0xA4])
            and len(build_combination_effect_frames(bytes(187))) == 12
        ),
    ),
    _check(
        "build_diy_in_scenes_multi_a3",
        lambda: bool(
            build_diy_in_scenes_multi_a3(0x62, bytes(range(20)))
            == [
                bytes.fromhex("a300010262000102030405060708090a0b0c0dc3"),
                bytes.fromhex("a3ff0e0f1011121300000000000000000000005d"),
            ]
            and build_diy_in_scenes_multi_a3(0x62, bytes(range(40)))[0][3] == 3
        ),
    ),
    _check(
        "build_diy_in_scenes_multi_a3",
        lambda: bool(
            (
                lambda f: (
                    len(f) == 3
                    and f[0][:6] == bytes([0xA3, 0x00, 0x01, 0x03, 0x62, 0x00])
                    and f[1][:3] == bytes([0xA3, 0x01, 14])
                    and f[2][:11] == bytes([163, 255, *list(range(31, 40))])
                    and f[2][11:19] == bytes(8)
                )
            )(build_diy_in_scenes_multi_a3(0x62, bytes(range(40))))
        ),
    ),
    _check(
        "build_diy_in_scenes_multi_a3",
        lambda: bool(
            (
                lambda f: (
                    len(f) == 2
                    and f[0][:6] == bytes([0xA3, 0x00, 0x01, 0x02, 0x54, 0x00])
                    and f[0][5:19] == bytes(range(14))
                    and f[1][:8] == bytes([0xA3, 0xFF, 14, 15, 16, 17, 18, 19])
                )
            )(build_diy_in_scenes_multi_a3(0x54, bytes(range(20))))
            and (
                lambda f: (
                    len(f) == 3
                    and f[0][:10] == bytes([0xA3, 0x00, 0x01, 0x02, 0x54, 0, 1, 2, 3, 4])
                    and f[1][:2] == bytes([0xA3, 0xFF])
                    and f[2][:7] == bytes([0x33, 0x05, 0x0A, 0x34, 0x12, 0x00, 0x00])
                )
            )(
                build_diy_in_scenes_multi_a3(
                    0x54,
                    bytes(range(5)),
                    bytes([0x33, 0x05, 0x0A, 0x34, 0x12, 0x00, 0x00])
                    + bytes(12)
                    + bytes([0x33 ^ 0x05 ^ 0x0A ^ 0x34 ^ 0x12]),
                )
            )
            and build_diy_in_scenes_multi_a3(0x58, bytes(3))[0][4] == 0x58
        ),
    ),
    _check(
        "build_daysync_node",
        lambda: bool(
            build_daysync_node(1, 1, 7, 0, 80, 0, [1]) == bytes([8, 0, 0, 0, 1, 1, 7, 0, 80, 1, 1, 1])
        ),
    ),
    _check(
        "build_daysync_node",
        lambda: bool(
            build_daysync_node(1, 1, 7, 0, 80, 0, [1]) == bytes([8, 0, 0, 0, 1, 1, 7, 0, 80, 1, 1, 1])
            and build_daysync_node(2, 8, 1, 0, 50, 1, b"")[4:8] == bytes([2, 8, 1, 0])
        ),
    ),
    _check(
        "build_daysync_scheme_incremental",
        lambda: bool(
            (
                lambda p: (
                    len(p) == 1
                    and p[0][0][:18] == bytes([0xA6, 0, 0, 1, 1, 1, 0, 1, 2, 3, 4, 5, 6, 7, 2, 0, 0x63, 3])
                    and p[0][0][17:36] == bytes([3, 0x81, 2, 1, 8, 0, 0, 0, 1, 1, 7, 0, 80, 1, 1, 1, 2, 2, 5])
                    and p[0][1] == bytes([0xA6, 0xFF, 0xFF, 0xA6])
                )
            )(
                build_daysync_scheme_incremental(
                    3, 0x81, [bytes([8, 0, 0, 0, 1, 1, 7, 0, 80, 1, 1, 1])], [2, 5], 100, 4, bytes(range(8))
                )
            )
            and (
                lambda p: (
                    len(p) == 1
                    and len(p[0]) == 3
                    and p[0][-1][:3] == bytes([0xA6, 0xFF, 0xFF])
                    and p[0][-1][-4:-1] == bytes([2, 2, 5])
                )
            )(
                build_daysync_scheme_incremental(
                    3,
                    0x81,
                    [
                        bytes([8, 0, 0, 0, 1, 1, 7, 0, 80, 1, 1, 1]),
                        bytes([8, 0, 0, 0, 2, 1, 8, 0, 80, 1, 1, 1]),
                    ],
                    [2, 5],
                    20,
                    1,
                    bytes(8),
                )
            )
            and build_daysync_scheme_incremental(1, 0x80 | 0x01 | 0x40, [], [3], 100, 4, bytes(8))[0][0][
                17:23
            ]
            == bytes([1, 0xC1, 2, 0, 1, 3])
            and (
                lambda p: (
                    len(p) == 2
                    and p[0][0][4:14] == bytes([2, 1, *list(range(8))])
                    and p[1][0][4:14] == bytes([2, 2, *list(range(8, 16))])
                    and p[1][0][17:19] == bytes(2)
                    and p[1][-1][-4:-1] == bytes([2, 0, 9])
                )
            )(
                build_daysync_scheme_incremental(
                    4, 0, [bytes(500), bytes(500)], [0, 9], 20, 1, [bytes(range(8)), bytes(range(8, 16))]
                )
            )
        ),
    ),
    _check(
        "build_daysync_space_check",
        lambda: bool(
            build_daysync_space_check(2, [], [(1, 20), (2, 24)])[0][:18]
            == bytes([0xA3, 0x00, 0x01, 0x02, 0x64, 2, 0, 2, 1, 20, 0, 0, 0, 2, 24, 0, 0, 0])
            and build_daysync_space_check(2, [], [(1, 20), (2, 24)])[1][:2] == bytes([0xA3, 0xFF])
        ),
    ),
    _check(
        "build_daysync_space_check",
        lambda: bool(
            build_daysync_space_check(2, [(3, 9)], [(1, 20), (2, 24)])
            == [
                bytes.fromhex("a3000102640201030900000002011400000002d8"),
                bytes.fromhex("a3ff180000000000000000000000000000000044"),
            ]
            and build_daysync_space_check(5, [], [(1, 20)])
            == [
                bytes.fromhex("a3000102640500010114000000000000000000d5"),
                bytes.fromhex("a3ff00000000000000000000000000000000005c"),
            ]
        ),
    ),
    _check(
        "build_daysync_space_check",
        lambda: bool(
            build_daysync_space_check(2, [], [(1, 20), (2, 24)])[0][:18]
            == bytes([0xA3, 0x00, 0x01, 0x02, 0x64, 2, 0, 2, 1, 20, 0, 0, 0, 2, 24, 0, 0, 0])
            and build_daysync_space_check(2, [], [(1, 20), (2, 24)])[1][:2] == bytes([0xA3, 0xFF])
            and len(build_daysync_space_check(1, [], [(i, 10) for i in range(1, 8)])) == 3
        ),
    ),
    _check(
        "build_daysync_space_check",
        lambda: bool(
            build_daysync_space_check(2, [], [(1, 20), (2, 24)])[0][:18]
            == bytes([0xA3, 0x00, 0x01, 0x02, 0x64, 2, 0, 2, 1, 20, 0, 0, 0, 2, 24, 0, 0, 0])
            and build_daysync_space_check(2, [], [(1, 20), (2, 24)])[1][:2] == bytes([0xA3, 0xFF])
            and len(build_daysync_space_check(1, [], [(i, 10) for i in range(1, 8)])) == 3
        ),
    ),
    _check(
        "build_multi_a3_v1",
        lambda: bool(
            build_multi_a3_v1(0x64, bytes(range(1, 4)))
            == [append_bcc(bytes([0xA3, 0x00, 0x01, 0x02, 0x64, 1, 2, 3])), append_bcc(bytes([0xA3, 0xFF]))]
            and [f[:2] for f in build_multi_a3_v1(0x65, bytes(range(40)))]
            == [bytes([0xA3, 0x00]), bytes([0xA3, 0x01]), bytes([0xA3, 0xFF])]
            and build_multi_a3_v1(0x65, bytes(range(40)))[0][3] == 3
            and build_multi_a3_v1(0x65, bytes(range(40)))[2][2:11] == bytes(range(31, 40))
        ),
    ),
    _check(
        "build_multi_a3_v1",
        lambda: bool(
            (
                lambda f: (
                    len(f) == 2
                    and f[0] == bytes([0xA3, 0x00, 0x01, 0x02, 0x50, 0x01, 0x02, 0x03] + [0x00] * 11 + [0xF0])
                    and f[1] == bytes([0xA3, 0xFF] + [0x00] * 17 + [0x5C])
                )
            )(build_multi_a3_v1(0x50, bytes([1, 2, 3])))
        ),
    ),
    _check(
        "build_multi_a3_v1",
        lambda: bool(
            build_multi_a3_v1(0x60, bytes(range(32)))[0][:6] == bytes([0xA3, 0x00, 0x01, 0x03, 0x60, 0x00])
            and build_multi_a3_v1(0x60, bytes(range(32)))[1][:3] == bytes([0xA3, 0x01, 0x0E])
            and build_multi_a3_v1(0x60, bytes(range(32)))[2][:4] == bytes([0xA3, 0xFF, 0x1F, 0x00])
            and len(build_multi_a3_v1(0x60, bytes(14))) == 2
        ),
    ),
    _check(
        "build_daysync_weather_group",
        lambda: bool(
            build_daysync_weather_group(4, [(2, 0, 0), (6, 0, 30)])[0][:13]
            == bytes([0xA3, 0x00, 0x01, 0x02, 0x65, 4, 2, 2, 0, 0, 6, 0, 30])
            and build_daysync_weather_group(4, [])[0][:7] == bytes([0xA3, 0x00, 0x01, 0x02, 0x65, 4, 0])
        ),
    ),
    _check(
        "build_daysync_weather_group",
        lambda: bool(
            build_daysync_weather_group(4, [(2, 0, 0), (6, 0, 30)])
            == [
                bytes.fromhex("a300010265040202000006001e000000000000d9"),
                bytes.fromhex("a3ff00000000000000000000000000000000005c"),
            ]
            and build_daysync_weather_group(4, [])
            == [
                bytes.fromhex("a3000102650400000000000000000000000000c1"),
                bytes.fromhex("a3ff00000000000000000000000000000000005c"),
            ]
        ),
    ),
    _check(
        "build_daysync_weather_group",
        lambda: bool(
            build_daysync_weather_group(4, [(2, 0, 0), (6, 0, 30)])[0][:13]
            == bytes([0xA3, 0x00, 0x01, 0x02, 0x65, 4, 2, 2, 0, 0, 6, 0, 30])
            and build_daysync_weather_group(4, [])[0][:6] == bytes([0xA3, 0x00, 0x01, 0x02, 0x65, 0x04])
        ),
    ),
    _check(
        "build_daysync_weather_group",
        lambda: bool(
            build_daysync_weather_group(4, [(2, 0, 0), (6, 0, 30)])[0][:13]
            == bytes([0xA3, 0x00, 0x01, 0x02, 0x65, 4, 2, 2, 0, 0, 6, 0, 30])
            and build_daysync_weather_group(4, [])[0][:7] == bytes([0xA3, 0x00, 0x01, 0x02, 0x65, 4, 0])
        ),
    ),
    _check(
        "build_h5310_close_warn_via_gw",
        lambda: bool(
            build_h5310_close_warn_via_gw(3, True)[:8]
            == bytes([0x33, 0x70, 0x03, 0x16, 0x01, 0x01, 0x00, 0x00])
            and build_h5310_close_warn_via_gw(3, False)
            == append_bcc(bytes([0x33, 0x70, 0x03]) + append_bcc(bytes([0x33, 0x16, 0x01, 0x02, 0x01]))[1:17])
        ),
    ),
    _check(
        "build_h5901_irrigation",
        lambda: bool(
            build_h5901_irrigation(1, 300)[:8] == bytes([0x33, 0x70, 0x01, 0x01, 0x01, 0x2C, 0x01, 0x00])
        ),
    ),
    _check(
        "build_h5901_switch_action",
        lambda: bool(
            build_h5901_switch_action(4)[:8] == bytes([0x33, 0x70, 0x04, 0x01, 0x00, 0x00, 0x00, 0x00])
        ),
    ),
    _check(
        "build_h5901_sync_triggers",
        lambda: bool(
            build_h5901_sync_triggers(2, True, False, True, False)[:8]
            == bytes([0x33, 0x70, 0x02, 0x19, 0x01, 0x00, 0x01, 0x00])
            and build_h5901_sync_triggers(-1, False, False, False, True)[2:7]
            == bytes([0x00, 0x19, 0x00, 0x00, 0x00])
        ),
    ),
    _check(
        "read_music_lib_player_state",
        lambda: bool(read_music_lib_player_state() == bytes([0xAA, 0x71] + [0] * 17 + [0xAA ^ 0x71])),
    ),
    _check(
        "read_music_lib_player_state",
        lambda: bool(read_music_lib_player_state() == bytes([0xAA, 0x71] + [0] * 17 + [0xAA ^ 0x71])),
    ),
    _check(
        "read_music_lib_player_state",
        lambda: bool(read_music_lib_player_state() == bytes([0xAA, 0x71] + [0] * 17 + [0xAA ^ 0x71])),
    ),
    _check(
        "build_music_lib_volume",
        lambda: bool(
            build_music_lib_volume(50) == bytes([0x33, 0x79, 0x32] + [0] * 16 + [0x33 ^ 0x79 ^ 0x32])
        ),
    ),
    _check(
        "build_music_lib_volume",
        lambda: bool(
            build_music_lib_volume(50)[:4] == bytes([0x33, 0x79, 0x32, 0x00])
            and build_music_lib_volume(50)[19] == (0x33 ^ 0x79 ^ 0x32)
        ),
    ),
    _check(
        "build_music_lib_volume",
        lambda: bool(
            build_music_lib_volume(50)[:4] == bytes([0x33, 0x79, 0x32, 0x00])
            and build_music_lib_volume(50)[19] == (0x33 ^ 0x79 ^ 0x32)
        ),
    ),
    _check(
        "read_music_lib_volume",
        lambda: bool(read_music_lib_volume() == bytes([0xAA, 0x79] + [0] * 17 + [0xAA ^ 0x79])),
    ),
    _check(
        "read_music_lib_volume",
        lambda: bool(read_music_lib_volume() == bytes([0xAA, 0x79] + [0] * 17 + [0xAA ^ 0x79])),
    ),
    _check(
        "read_music_lib_volume",
        lambda: bool(read_music_lib_volume() == bytes([0xAA, 0x79] + [0] * 17 + [0xAA ^ 0x79])),
    ),
    _check(
        "read_diy_create_gif",
        lambda: bool(
            read_diy_create_gif(0x0102, 5) == bytes.fromhex("ac7b0a02010105" + "00" * 12 + "da")
            and read_diy_create_gif(254) == bytes.fromhex("ac7b0afe0001" + "00" * 13 + "22")
        ),
    ),
    _check(
        "build_carousel_apply_mode",
        lambda: bool(
            build_carousel_apply_mode(0x01020304, 7, 2, 1)[:14]
            == bytes([0x33, 0x05, 0x82, 0x04, 0x03, 0x02, 0x01, 0x07, 0x00, 0x00, 0x00, 0x02, 0x01, 0x00])
            and build_carousel_apply_mode(0x01020304, 7, 2, 1)[19]
            == (0x33 ^ 0x05 ^ 0x82 ^ 0x04 ^ 0x03 ^ 0x02 ^ 0x01 ^ 0x07 ^ 0x02 ^ 0x01)
            and build_carousel_apply_mode(5)[3:13] == bytes([5, 0, 0, 0, 0, 0, 0, 0, 0, 0])
        ),
    ),
    _check(
        "build_carousel_apply_mode",
        lambda: bool(
            build_carousel_apply_mode(0x01020304, 7, 1, 0)[:13]
            == bytes([0x33, 0x05, 0x82, 0x04, 0x03, 0x02, 0x01, 0x07, 0x00, 0x00, 0x00, 0x01, 0x00])
            and build_carousel_apply_mode(0x01020304, 7, 1, 0)[13:19] == bytes(6)
        ),
    ),
    _check(
        "build_carousel_apply_mode",
        lambda: bool(
            build_carousel_apply_mode(0x01020304, 7, 1, 0)[:13]
            == bytes([0x33, 0x05, 0x82, 0x04, 0x03, 0x02, 0x01, 0x07, 0x00, 0x00, 0x00, 0x01, 0x00])
        ),
    ),
    _check(
        "build_display_remind_effect",
        lambda: bool(
            build_display_remind_effect(3, 2, True, 1, 0x01020304, 2, 0x0A0B)[:17]
            == bytes([0x33, 0x83, 0x01, 3, 2, 1, 1, 4, 3, 2, 1, 2, 0x0B, 0x0A, 0, 0, 0])
        ),
    ),
    _check(
        "build_display_remind_effect",
        lambda: bool(
            build_display_remind_effect(3, 2, True, 1, 0x01020304, 2, 0x0A0B)[:17]
            == bytes([0x33, 0x83, 0x01, 3, 2, 1, 1, 4, 3, 2, 1, 2, 0x0B, 0x0A, 0, 0, 0])
            and build_display_remind_effect(3, 2, True, 1, 0x01020304, 2, 0x0A0B)[19]
            == (0x33 ^ 0x83 ^ 0x01 ^ 3 ^ 2 ^ 1 ^ 1 ^ 4 ^ 3 ^ 2 ^ 1 ^ 2 ^ 0x0B ^ 0x0A)
        ),
    ),
    _check(
        "build_display_remind_effect",
        lambda: bool(
            build_display_remind_effect(3, 2, True, 1, 0x01020304, 2, 0x0A0B)[:17]
            == bytes([0x33, 0x83, 0x01, 3, 2, 1, 1, 4, 3, 2, 1, 2, 0x0B, 0x0A, 0, 0, 0])
        ),
    ),
    _check(
        "build_display_remind_effect",
        lambda: bool(
            build_display_remind_effect(3, 2, True, 1, 0x01020304, 2, 0x0A0B)[:17]
            == bytes([0x33, 0x83, 0x01, 3, 2, 1, 1, 4, 3, 2, 1, 2, 0x0B, 0x0A, 0, 0, 0])
        ),
    ),
    _check(
        "build_display_remind_effect",
        lambda: bool(
            build_display_remind_effect(3, 2, True, 1, 0x01020304, 2, 0x0A0B)[:17]
            == bytes([0x33, 0x83, 0x01, 3, 2, 1, 1, 4, 3, 2, 1, 2, 0x0B, 0x0A, 0, 0, 0])
        ),
    ),
    _check(
        "build_display_remind_global",
        lambda: bool(
            build_display_remind_global(False, 22, 0, 7, 30, 0x7F, True, False, True)[:13]
            == bytes([0x33, 0x83, 0x00, 0x00, 22, 0, 7, 30, 0x7F, 1, 0, 1, 0])
        ),
    ),
    _check(
        "build_display_remind_global",
        lambda: bool(
            build_display_remind_global(False, 22, 0, 7, 30, 0x7F, True, False, True)
            == bytes(
                [0x33, 0x83, 0x00, 0x00, 22, 0, 7, 30, 0x7F, 1, 0, 1]
                + [0] * 7
                + [0x33 ^ 0x83 ^ 22 ^ 7 ^ 30 ^ 0x7F ^ 1 ^ 1]
            )
            and build_display_remind_global(True, 0, 0, 0, 0, 0, False, False, False)[2:4]
            == bytes([0x00, 0x01])
        ),
    ),
    _check(
        "build_display_remind_global",
        lambda: bool(
            build_display_remind_global(False, 22, 0, 7, 30, 0x7F, True, False, True)[:13]
            == bytes([0x33, 0x83, 0x00, 0x00, 22, 0, 7, 30, 0x7F, 1, 0, 1, 0])
        ),
    ),
    _check(
        "build_display_remind_global",
        lambda: bool(
            build_display_remind_global(False, 22, 0, 7, 30, 0x7F, True, False, True)[:13]
            == bytes([0x33, 0x83, 0x00, 0x00, 22, 0, 7, 30, 0x7F, 1, 0, 1, 0])
        ),
    ),
    _check(
        "build_display_remind_global",
        lambda: bool(
            build_display_remind_global(False, 22, 0, 7, 30, 0x7F, True, False, True)[:13]
            == bytes([0x33, 0x83, 0x00, 0x00, 22, 0, 7, 30, 0x7F, 1, 0, 1, 0])
        ),
    ),
    _check(
        "build_mic_set_rgb",
        lambda: bool(
            build_mic_set_rgb(0x10, 0x20, 0x30) == bytes([0xA5, 0x02, 0x83, 0x10, 0x20, 0x30, 0x8A])
        ),
    ),
    _check(
        "build_mic_set_rgb",
        lambda: bool(
            build_mic_set_rgb(0x10, 0x20, 0x30) == bytes([0xA5, 0x02, 0x83, 0x10, 0x20, 0x30, 0x8A])
            and build_mic_set_rgb(0xFF, 0x00, 0x00) == bytes([0xA5, 0x02, 0x83, 0xFF, 0x00, 0x00, 0x29])
            and len(build_mic_set_rgb(1, 2, 3)) == 7
        ),
    ),
    _check(
        "build_mic_set_rgb",
        lambda: bool(
            build_mic_set_rgb(0x10, 0x20, 0x30) == bytes([0xA5, 0x02, 0x83, 0x10, 0x20, 0x30, 0x8A])
            and build_mic_set_rgb(0xFF, 0xFF, 0xFF) == bytes([0xA5, 0x02, 0x83, 0xFF, 0xFF, 0xFF, 0x27])
            and len(build_mic_set_rgb(1, 2, 3)) == 7
        ),
    ),
    _check(
        "read_display_remind",
        lambda: bool(
            read_display_remind()[:3] == bytes([0xAC, 0x83, 0x00])
            and read_display_remind()[19] == 0xAC ^ 0x83
        ),
    ),
    _check(
        "read_display_remind",
        lambda: bool(read_display_remind() == bytes([0xAC, 0x83] + [0] * 17 + [0xAC ^ 0x83])),
    ),
    _check(
        "read_display_remind",
        lambda: bool(
            read_display_remind()[:3] == bytes([0xAC, 0x83, 0x00])
            and read_display_remind()[19] == 0xAC ^ 0x83
        ),
    ),
    _check(
        "read_display_remind",
        lambda: bool(
            read_display_remind()[:3] == bytes([0xAC, 0x83, 0x00])
            and read_display_remind()[19] == 0xAC ^ 0x83
        ),
    ),
    _check(
        "build_alarm_clock_apply_effect",
        lambda: bool(
            (
                lambda f: (
                    len(f) == 3
                    and f[0][:3] == bytes([0x33, 0x01, 0x01])
                    and f[1][:2] == bytes([0xA3, 0x00])
                    and f[2][:3] == bytes([0x33, 0x84, 0x00])
                )
            )(
                build_alarm_clock_apply_effect(
                    [bytes([0xA3, 0x00]) + bytes(18)],
                    bytes([0x33, 0x84, 0x00]) + bytes(17),
                    [bytes([0x33, 0x01, 0x01]) + bytes(17)],
                )
            )
            and (
                lambda f: (
                    len(f) == 2 and f[0][:2] == bytes([0xA3, 0x00]) and f[1][:3] == bytes([0x33, 0x84, 0x00])
                )
            )(
                build_alarm_clock_apply_effect(
                    [bytes([0xA3, 0x00]) + bytes(18)], bytes([0x33, 0x84, 0x00]) + bytes(17)
                )
            )
            and len(build_alarm_clock_apply_effect([], bytes([0x33, 0x84, 0x00]) + bytes(17))) == 1
        ),
    ),
    _check(
        "build_alarm_clock_delete",
        lambda: bool(
            build_alarm_clock_delete(5)
            == bytes([0x33, 0x84, 0x02, 0x05] + [0] * 15 + [0x33 ^ 0x84 ^ 0x02 ^ 0x05])
        ),
    ),
    _check(
        "build_alarm_clock_delete",
        lambda: bool(build_alarm_clock_delete(5)[:5] == bytes([0x33, 0x84, 0x02, 0x05, 0x00])),
    ),
    _check(
        "build_alarm_clock_delete",
        lambda: bool(build_alarm_clock_delete(5)[:5] == bytes([0x33, 0x84, 0x02, 0x05, 0x00])),
    ),
    _check(
        "build_alarm_clock_edit",
        lambda: bool(
            build_alarm_clock_edit(
                0, 5, True, True, False, True, 7, 30, 0x3E, 80, 1, 0x1234, 60, 0x0A0B0C0D, 2
            )[:19]
            == bytes(
                [
                    0x33,
                    0x84,
                    0x00,
                    0x05,
                    0xD0,
                    7,
                    30,
                    0x3E,
                    80,
                    1,
                    0x34,
                    0x12,
                    60,
                    0x0D,
                    0x0C,
                    0x0B,
                    0x0A,
                    2,
                    0,
                ]
            )
            and build_alarm_clock_edit(1, 1, False, False, True, False, 6, 0, 0, 0, 0, 0, 0, 0, 0)[2:5]
            == bytes([0x01, 0x01, 0x20])
        ),
    ),
    _check(
        "build_alarm_clock_edit",
        lambda: bool(
            build_alarm_clock_edit(
                0, 5, True, True, False, True, 7, 30, 0x3E, 80, 1, 0x1234, 60, 0x0A0B0C0D, 2
            )[:19]
            == bytes(
                [
                    0x33,
                    0x84,
                    0x00,
                    0x05,
                    0xD0,
                    7,
                    30,
                    0x3E,
                    80,
                    1,
                    0x34,
                    0x12,
                    60,
                    0x0D,
                    0x0C,
                    0x0B,
                    0x0A,
                    2,
                    0,
                ]
            )
        ),
    ),
    _check(
        "build_alarm_clock_edit",
        lambda: bool(
            build_alarm_clock_edit(
                0, 5, True, True, False, True, 7, 30, 0x3E, 80, 1, 0x1234, 60, 0x0A0B0C0D, 2
            )[:19]
            == bytes(
                [
                    0x33,
                    0x84,
                    0x00,
                    0x05,
                    0xD0,
                    7,
                    30,
                    0x3E,
                    80,
                    1,
                    0x34,
                    0x12,
                    60,
                    0x0D,
                    0x0C,
                    0x0B,
                    0x0A,
                    2,
                    0,
                ]
            )
        ),
    ),
    _check(
        "build_alarm_clock_edit_days",
        lambda: bool(
            build_alarm_clock_edit_days(
                0, 5, True, True, False, True, 7, 30, [1, 0, 0, 0, 1, 0, 0], 80, 1, 0x1234, 60, 0x0A0B0C0D, 2
            )[:19]
            == bytes(
                [
                    0x33,
                    0x84,
                    0x00,
                    0x05,
                    0xD0,
                    7,
                    30,
                    0x91,
                    80,
                    1,
                    0x34,
                    0x12,
                    60,
                    0x0D,
                    0x0C,
                    0x0B,
                    0x0A,
                    2,
                    0,
                ]
            )
            and build_alarm_clock_edit_days(
                1, 1, True, False, False, False, 6, 0, [True] * 7, 50, 0, 0, 0, 0, 0
            )[7]
            == 0x00
            and build_alarm_clock_edit_days(
                1, 1, True, False, False, False, 6, 0, [False] * 7, 50, 0, 0, 0, 0, 0
            )[7]
            == 0x80
            and build_alarm_clock_edit_days(
                1, 1, True, False, False, False, 6, 0, [0, 0, 0, 0, 0, 0, 1], 50, 0, 0, 0, 0, 0
            )[7]
            == 0xC0
        ),
    ),
    _check(
        "build_alarm_clock_switch",
        lambda: bool(
            build_alarm_clock_switch(5, True, False, False, False)[:6]
            == bytes([0x33, 0x84, 0x03, 0x05, 0x80, 0x00])
            and build_alarm_clock_switch(2, False, True, True, True)[4] == 0x70
        ),
    ),
    _check(
        "build_alarm_clock_switch",
        lambda: bool(
            build_alarm_clock_switch(5, True, False, False, False)[:5]
            == bytes([0x33, 0x84, 0x03, 0x05, 0x80])
        ),
    ),
    _check(
        "build_alarm_clock_switch",
        lambda: bool(
            build_alarm_clock_switch(5, True, False, False, False)[:5]
            == bytes([0x33, 0x84, 0x03, 0x05, 0x80])
        ),
    ),
    _check(
        "read_alarm_clock_list",
        lambda: bool(read_alarm_clock_list() == bytes([0xAC, 0x84] + [0] * 17 + [0xAC ^ 0x84])),
    ),
    _check(
        "read_alarm_clock_list",
        lambda: bool(
            read_alarm_clock_list()[:3] == bytes([0xAC, 0x84, 0x00])
            and read_alarm_clock_list()[19] == 0xAC ^ 0x84
        ),
    ),
    _check(
        "read_alarm_clock_list",
        lambda: bool(
            read_alarm_clock_list()[:3] == bytes([0xAC, 0x84, 0x00])
            and read_alarm_clock_list()[19] == 0xAC ^ 0x84
        ),
    ),
    _check(
        "build_alarm_clock_ring_action",
        lambda: bool(
            build_alarm_clock_ring_action(True)[:4] == bytes([0x33, 0x85, 0x01, 0x00])
            and build_alarm_clock_ring_action(False)[:3] == bytes([0x33, 0x85, 0x00])
        ),
    ),
    _check(
        "build_alarm_clock_ring_action",
        lambda: bool(
            build_alarm_clock_ring_action(True) == bytes([0x33, 0x85, 0x01] + [0] * 16 + [0x33 ^ 0x85 ^ 0x01])
            and build_alarm_clock_ring_action(False)[:3] == bytes([0x33, 0x85, 0x00])
            and build_alarm_clock_ring_action(False)[19] == 0x33 ^ 0x85
        ),
    ),
    _check(
        "build_alarm_clock_ring_action",
        lambda: bool(
            build_alarm_clock_ring_action(True)[:4] == bytes([0x33, 0x85, 0x01, 0x00])
            and build_alarm_clock_ring_action(False)[:3] == bytes([0x33, 0x85, 0x00])
        ),
    ),
    _check(
        "build_alarm_clock_ring_action",
        lambda: bool(
            build_alarm_clock_ring_action(True)[:4] == bytes([0x33, 0x85, 0x01, 0x00])
            and build_alarm_clock_ring_action(False)[:3] == bytes([0x33, 0x85, 0x00])
        ),
    ),
    _check(
        "build_alarm_clock_ring_action",
        lambda: bool(
            build_alarm_clock_ring_action(True)[:4] == bytes([0x33, 0x85, 0x01, 0x00])
            and build_alarm_clock_ring_action(False)[:3] == bytes([0x33, 0x85, 0x00])
        ),
    ),
    _check(
        "build_diy_create_gif",
        lambda: bool(
            build_diy_create_gif(254, 0x11223344)[:10]
            == bytes([0x33, 0x86, 0x01, 0xFE, 0x00, 0x44, 0x33, 0x22, 0x11, 0x00])
        ),
    ),
    _check(
        "build_diy_create_gif",
        lambda: bool(
            build_diy_create_gif(254, 0x11223344)[:10]
            == bytes([0x33, 0x86, 0x01, 0xFE, 0x00, 0x44, 0x33, 0x22, 0x11, 0x00])
            and build_diy_create_gif(254, 0x11223344)[19]
            == (0x33 ^ 0x86 ^ 0x01 ^ 0xFE ^ 0x44 ^ 0x33 ^ 0x22 ^ 0x11)
        ),
    ),
    _check(
        "build_diy_create_gif_v1",
        lambda: bool(
            build_diy_create_gif_v1(0x1234, 0x01020304)
            == bytes.fromhex("338601341204030201" + "00" * 10 + "96")
        ),
    ),
    _check(
        "read_special_info_a1",
        lambda: bool(
            read_special_info_a1() == bytes([0xAC, 0xA1] + [0] * 17 + [0x0D])
            and read_special_info_a1() == build_status_request([], command=0xA1)
        ),
    ),
    _check(
        "read_bulb_group_color",
        lambda: bool(
            read_bulb_group_color(1) == bytes.fromhex("aaa2010000000000000000000000000000000009")
            and read_bulb_group_color(4)[:4] == bytes([0xAA, 0xA2, 0x04, 0x00])
        ),
    ),
    _check(
        "read_bulb_group_color",
        lambda: bool(
            read_bulb_group_color(1) == bytes.fromhex("aaa2010000000000000000000000000000000009")
            and read_bulb_group_color(4) == bytes([0xAA, 0xA2, 0x04] + [0] * 16 + [0xAA ^ 0xA2 ^ 0x04])
        ),
    ),
    _check(
        "read_bulb_group_color",
        lambda: bool(read_bulb_group_color(1) == bytes.fromhex("aaa2010000000000000000000000000000000009")),
    ),
    _check(
        "read_color_group",
        lambda: bool(
            read_color_group(1) == bytes.fromhex("aaa501000000000000000000000000000000000e")
            and read_color_group(3) == bytes.fromhex("aaa503000000000000000000000000000000000c")
        ),
    ),
    _check(
        "read_color_group",
        lambda: bool(
            read_color_group(2) == bytes([0xAA, 0xA5, 0x02] + [0] * 16 + [0xAA ^ 0xA5 ^ 0x02])
            and read_color_group(3) == bytes.fromhex("aaa503000000000000000000000000000000000c")
        ),
    ),
    _check(
        "read_color_group",
        lambda: bool(
            read_color_group(3) == bytes.fromhex("aaa503000000000000000000000000000000000c")
            and read_color_group(1) == bytes.fromhex("aaa501000000000000000000000000000000000e")
        ),
    ),
    _check(
        "build_abs_music_blob",
        lambda: bool(
            (
                lambda f: (
                    len(f) == 3
                    and f[0][:8] == bytes([0xA3, 0x00, 0x01, 0x02, 0x42, 0xAA, 0xBB, 0x00])
                    and f[1] == append_bcc(bytes([0xA3, 0xFF]))
                    and f[2] == append_bcc(bytes([0x33, 0x05, 0x16, 0x2C, 0x01, 0xFF]))
                )
            )(build_abs_music_blob(bytes([0xAA, 0xBB]), 300))
            and (
                lambda f: (
                    len(f) == 4
                    and f[0][3] == 3
                    and f[0][5:19] == bytes(range(14))
                    and f[1][:3] == bytes([0xA3, 0x01, 14])
                    and f[2][:3] == bytes([0xA3, 0xFF, 31])
                    and f[3][:6] == bytes([0x33, 0x05, 0x16, 0x00, 0x00, 0xFF])
                )
            )(build_abs_music_blob(bytes(range(32)), 0))
        ),
    ),
    _check(
        "build_breath_train_multi_a3",
        lambda: bool(
            build_breath_train_multi_a3(bytes([1, 2, 3]))[0][:8]
            == bytes([0xA3, 0x00, 0x01, 0x02, 0x70, 1, 2, 3])
            and [f[:2] for f in build_breath_train_multi_a3(bytes(range(40)))]
            == [bytes([0xA3, 0x00]), bytes([0xA3, 0x01]), bytes([0xA3, 0xFF])]
            and len(build_breath_train_multi_a3(bytes(31))) == 2
            and build_breath_train_multi_a3(bytes(32))[0][3] == 3
        ),
    ),
    _check(
        "build_breath_train_multi_a3",
        lambda: bool(
            build_breath_train_multi_a3(bytes([1, 2, 3]))[0][:8]
            == bytes([0xA3, 0x00, 0x01, 0x02, 0x70, 1, 2, 3])
            and [f[:2] for f in build_breath_train_multi_a3(bytes(range(40)))]
            == [bytes([0xA3, 0x00]), bytes([0xA3, 0x01]), bytes([0xA3, 0xFF])]
        ),
    ),
    _check(
        "build_color_strip_apply",
        lambda: bool(
            build_color_strip_apply([0xFFFF0000, 0xFF00FF00], [100, 100])
            == [
                bytes.fromhex("a30001024003000100ff00010001ff00000001e3"),
                bytes.fromhex("a3ff02640001000000000000000000000000003b"),
                bytes.fromhex("3305150000000000000000000000000000000023"),
            ]
            and build_color_strip_apply([0xFFFF0000], None, 10)[-1]
            == bytes.fromhex("33050c000000000000000000000000000000003a")
        ),
    ),
    _check(
        "build_color_strip_multi",
        lambda: bool(
            build_color_strip_multi([0xFFFF0000, 0xFF00FF00, 0xFF0000FF])
            == [
                bytes.fromhex("a30001024003000100ff00010001ff00000000e2"),
                bytes.fromhex("a3ff010000ff02000000000000000000000000a0"),
            ]
            and build_color_strip_multi([0xFFFF0000, 0xFFFF0000, 0xFF00FF00, 0xFF0000FF], [100, 100, 50, 20])
            == [
                bytes.fromhex("a30001034006000100ff00020002ff00000001e7"),
                bytes.fromhex("a30100010000ff03010132020102640001010109"),
                bytes.fromhex("a3ff14030000000000000000000000000000004b"),
            ]
        ),
    ),
    _check(
        "build_color_tem_strip_apply",
        lambda: bool(
            build_color_tem_strip_apply([0xFFFFFFFF, 0xFFFFFFFF], {0xFFFFFFFF: 2700}, None, 10)
            == [
                bytes.fromhex("a3000102400102020a8cffffff00010000000099"),
                bytes.fromhex("a3ff00000000000000000000000000000000005c"),
                bytes.fromhex("33050c000000000000000000000000000000003a"),
            ]
        ),
    ),
    _check(
        "build_color_tem_strip_multi",
        lambda: bool(
            build_color_tem_strip_multi(
                [0xFFFF0000, 0xFFFFFFFF, 0xFFFFFFFF, 0xFF00FF00], {0xFFFFFFFF: 6500}, [100, 100, 100, 100]
            )
            == [
                bytes.fromhex("a30001024004000100ff00030001ff00000001e6"),
                bytes.fromhex("a3ff04640001020302021964ffffff01020000bd"),
            ]
            and build_color_tem_strip_multi([0xFFFF0000], {})
            == [
                bytes.fromhex("a300010240010001ff000000000000000000001f"),
                bytes.fromhex("a3ff00000000000000000000000000000000005c"),
            ]
        ),
    ),
    _check(
        "build_gradient_color_strip_apply",
        lambda: bool(
            build_gradient_color_strip_apply([0xFFFF0000, 0xFFFFFFFF], 2, {0xFFFFFFFF: 6500}, [100, 50])
            == [
                bytes.fromhex("a300010240040301ff0000ff0000ff0000000118"),
                bytes.fromhex("a3ff013201010164000201196400196401000008"),
                bytes.fromhex("3305150000000000000000000000000000000023"),
            ]
            and build_gradient_color_strip_apply(
                [0xFFFF0000, 0xFF00FF00, 0xFF0000FF, 0xFFFFFFFF, 0xFFFFFFFF, 0xFFFFFFFF],
                2,
                {0xFFFFFFFF: 6500},
                [100, 100, 100, 50, 50, 50],
            )
            == [
                bytes.fromhex("a300010240040301ff000000ff000000ff000118"),
                bytes.fromhex("a3ff013201010164000201196400196401000008"),
                bytes.fromhex("3305150000000000000000000000000000000023"),
            ]
        ),
    ),
    _check(
        "build_gradient_color_tem_strip_multi",
        lambda: bool(
            build_gradient_color_tem_strip_multi(
                [
                    0xFFFF0000,
                    0xFF00FF00,
                    0xFF0000FF,
                    0xFFFFFFFF,
                    0xFFFFFFFF,
                    0xFFFFFFFF,
                    0xFFFF0000,
                    0xFF00FF00,
                    0xFF0000FF,
                ],
                {0xFFFFFFFF: 6500},
                [100, 50, 100],
            )
            == [
                bytes.fromhex("a300010240040302ff000000ff000000ff000218"),
                bytes.fromhex("a3ff010132010102640002020119640019640108"),
            ]
        ),
    ),
    _check(
        "build_h60ax_new_music_multi",
        lambda: bool(
            (
                lambda f: (
                    len(f) == 3
                    and f[0][:9] == bytes([0xA3, 0x00, 0x01, 0x02, 0x41, 0x32, 0x01, 0x02, 0x03])
                    and f[1] == append_bcc(bytes([0xA3, 0xFF]))
                    and f[2][:5] == bytes([0x33, 0x05, 0x13, 0x05, 0x64])
                    and f[2][19] == 0x33 ^ 0x05 ^ 0x13 ^ 0x05 ^ 0x64
                )
            )(build_h60ax_new_music_multi(0x32, bytes([1, 2, 3])))
            and (
                lambda f: (
                    len(f) == 4
                    and f[0][3] == 3
                    and f[0][6:19] == bytes(range(13))
                    and f[1][:3] == bytes([0xA3, 0x01, 13])
                    and f[2][:4] == bytes([0xA3, 0xFF, 30, 31])
                    and f[2][4:19] == bytes(15)
                )
            )(build_h60ax_new_music_multi(0x32, bytes(range(32))))
        ),
    ),
    _check(
        "build_h61d3_light_recognition_fixed",
        lambda: bool(
            build_h61d3_light_recognition_fixed()
            == [
                bytes.fromhex("a30001025d64ffffff2c010202010106ff0000b2"),
                bytes.fromhex("a3ffffff0000ff000000ff00ffff8b00ff000028"),
            ]
        ),
    ),
    _check(
        "build_hollowlamp_local_colors",
        lambda: bool(
            build_hollowlamp_local_colors([(255, 0, 0, 0), (255, 180, 107, 2700), (0, 0, 255, 0)])
            == [
                bytes.fromhex("a3000102a503ff00000000ffb46b0a8c0000ffa0"),
                bytes.fromhex("a3ff00000000000000000000000000000000005c"),
            ]
        ),
    ),
    _check(
        "build_hollowlamp_local_colors",
        lambda: bool(
            build_hollowlamp_local_colors([(255, 0, 0, 0), (255, 180, 107, 2700), (0, 0, 255, 0)])
            == [
                bytes.fromhex("a3000102a503ff00000000ffb46b0a8c0000ffa0"),
                bytes.fromhex("a3ff00000000000000000000000000000000005c"),
            ]
            and build_hollowlamp_local_colors([(255, 0, 0, 0), (0, 255, 0, 0), (0, 0, 0, 6500)])[1]
            == bytes.fromhex("a3ff196400000000000000000000000000000021")
        ),
    ),
    _check(
        "build_hollowlamp_multi_music_v1",
        lambda: bool(
            (
                lambda f: (
                    len(f) == 3
                    and f[0] == bytes.fromhex("a3000102414b02ff000000ff00000000000000a8")
                    and f[2] == bytes.fromhex("3305134b0063000000000000000000000000000d")
                )
            )(build_hollowlamp_multi_music_v1(0x4B, [(255, 0, 0), (0, 255, 0)], 99, 0))
            and build_hollowlamp_multi_music_v1(
                0x4B,
                [
                    (255, 0, 0),
                    (255, 0x7D, 0),
                    (255, 255, 0),
                    (0, 255, 0),
                    (0, 0, 255),
                    (0, 255, 255),
                    (0x8B, 0, 255),
                ],
            )
            == [
                bytes.fromhex("a3000102414b07ff0000ff7d00ffff0000ff002f"),
                bytes.fromhex("a3ff0000ff00ffff8b00ff0000000000000000d7"),
            ]
            and build_hollowlamp_multi_music_v1(0x4B, [], 99) == []
        ),
    ),
    _check(
        "build_multi_a3_v1_group",
        lambda: bool(
            (
                lambda f: (
                    len(f) == 3
                    and f[0][:5] == bytes([0xA3, 0x00, 0x01, 0x03, 0x50])
                    and f[0][5:19] == bytes(range(14))
                    and f[1][:3] == bytes([0xA3, 0x01, 14])
                    and f[2][:2] == bytes([0xA3, 0xFF])
                    and f[2][2:19] == bytes([31, 32, 33, 34, 35, *list(range(19, 31))])
                )
            )(build_multi_a3_v1_group(0x50, bytes(range(36))))
            and build_multi_a3_v1_group(0x50, bytes(range(20)))[1][2:19] == bytes(range(14, 20)) + bytes(11)
            and build_multi_a3_v1_group([0x02, 0x01], bytes(range(33)))[0][4:6] == bytes([0x02, 0x01])
            and build_multi_a3_v1_group([0x02, 0x01], bytes(range(33)))[2][2:19]
            == bytes([30, 31, 32, *list(range(16, 30))])
        ),
    ),
    _check(
        "build_music_h1771",
        lambda: bool(
            build_music_h1771(4, [(255, 0, 0), (0, 0, 255)], [0, 9], 60)
            == [
                bytes.fromhex("a3000102410402ff00000000ff000000000000e7"),
                bytes.fromhex("a3ff00000000000000000000000000000000005c"),
                bytes.fromhex("330513043c00000000000000000000000000001d"),
            ]
            and build_music_h1771(52, [(1, 2, 3)], [5, 6], 60)[0]
            == bytes.fromhex("a3000102413401010203050600000000000000d7")
            and build_music_h1771(52, [(1, 2, 3)], [5, 6], 60, preview=True)
            == [bytes.fromhex("330513343c00000000000000000000000000002d")]
        ),
    ),
    _check(
        "build_music_h6057",
        lambda: bool(
            build_music_h6057(57, [(255, 0, 0)], [1, 0, 0], 99)
            == [
                bytes.fromhex("a3000102413901ff000001000000000000000027"),
                bytes.fromhex("a3ff00000000000000000000000000000000005c"),
                bytes.fromhex("330513396300000000000000000000000000007f"),
            ]
            and build_music_h6057(57, [(255, 0, 0)], [1, 0, 0], 99, preview=True)
            == [bytes.fromhex("330513396300000000000000000000000000007f")]
        ),
    ),
    _check(
        "build_music_h6059",
        lambda: bool(
            build_music_h6059(75, [(255, 0, 0)], [1], 80)
            == [
                bytes.fromhex("a3000102414b01ff000001000000000000000055"),
                bytes.fromhex("a3ff00000000000000000000000000000000005c"),
                bytes.fromhex("3305134b0150000000000000000000000000003f"),
            ]
            and build_music_h6059(76, [(255, 0, 0)], [1, 2], 50, preview=True)
            == [bytes.fromhex("3305134c01023200000000000000000000000058")]
            and build_music_h6059(76, [(0, 0, 255)], [], 50)[-1]
            == bytes.fromhex("3305134c3200000000000000000000000000005b")
        ),
    ),
    _check(
        "build_music_mode_compose_v2",
        lambda: bool(
            (
                lambda f: (
                    len(f) == 3
                    and f[0] == append_bcc(bytes([0xA3, 0x00, 0x01, 0x02, 0x41, 0x48]) + bytes(range(13)))
                    and f[1] == append_bcc(bytes([0xA3, 0xFF]) + bytes(range(13, 20)))
                    and f[2] == append_bcc(bytes([0x33, 0x05, 0x13, 0x48, 0x32]))
                )
            )(build_music_mode_compose_v2(72, 50, bytes(range(20))))
            and (
                lambda f: (
                    len(f) == 4
                    and f[0][3] == 3
                    and f[1][:3] == bytes([0xA3, 0x01, 13])
                    and f[2][:3] == bytes([0xA3, 0xFF, 30])
                    and f[2][3:19] == bytes(16)
                    and f[3][:6] == bytes([0x33, 0x05, 0x13, 0x63, 0x0A, 0x00])
                )
            )(build_music_mode_compose_v2(99, 10, bytes(range(31))))
            and (
                lambda f: (
                    len(f) == 3
                    and f[0][:7] == bytes([0xA3, 0x00, 0x01, 0x02, 0x41, 0x05, 0x01])
                    and f[0][7:19] == bytes(12)
                    and f[2][:5] == bytes([0x33, 0x05, 0x13, 0x05, 0x64])
                )
            )(build_music_mode_compose_v2(5, 100, bytes([1])))
        ),
    ),
    _check(
        "build_music_multi_a3_v2",
        lambda: bool(
            build_music_multi_a3_v2(0x32, bytes(range(13)))
            == [
                bytes.fromhex("a30001024132000102030405060708090a0b0cdf"),
                bytes.fromhex("a3ff00000000000000000000000000000000005c"),
            ]
            and build_music_multi_a3_v2(0x32, bytes(range(14)))[1]
            == bytes.fromhex("a3ff0d0000000000000000000000000000000051")
            and len(build_music_multi_a3_v2(0x32, bytes(31))) == 3
            and build_music_multi_a3_v2(0x32, bytes(31))[0][3] == 3
        ),
    ),
    _check(
        "build_music_v2_then_mode",
        lambda: bool(
            build_music_v2_then_mode(0x4B, bytes([1, 255, 0, 0, 1]), bytes([0x13, 0x4B, 0x01, 0x50]))
            == [
                bytes.fromhex("a3000102414b01ff000001000000000000000055"),
                bytes.fromhex("a3ff00000000000000000000000000000000005c"),
                bytes.fromhex("3305134b0150000000000000000000000000003f"),
            ]
        ),
    ),
    _check(
        "build_set_ai_action",
        lambda: bool(
            (
                lambda f: (
                    len(f) == 2
                    and f[0][:19]
                    == bytes(
                        [
                            0xA3,
                            0x00,
                            0x01,
                            0x02,
                            0x58,
                            0x01,
                            0x0C,
                            0x00,
                            0x02,
                            0x01,
                            0x04,
                            0x03,
                            0x05,
                            0x00,
                            0x01,
                            0xFF,
                            0x00,
                            0x80,
                            0x0A,
                        ]
                    )
                    and f[1][:3] == bytes([0xA3, 0xFF, 0x14])
                    and f[1][3:19] == bytes(16)
                )
            )(build_set_ai_action(True, 0x0102, 0x0304, 5, [0xFF0080], 10, 20))
            and build_set_ai_action(False, 0, 0, 0, [], 0, 0)[0][:16]
            == bytes([0xA3, 0x00, 0x01, 0x02, 0x58, 0x00, 0x09, 0x00, 0, 0, 0, 0, 0, 0, 0x00, 0x00])
            and len(build_set_ai_action(True, 0, 0, 1, [0x010203] * 7, 1, 1)) == 3
            and len(build_set_ai_action(True, 0, 0, 1, [0x010203] * 6, 1, 1)) == 2
        ),
    ),
    _check(
        "build_set_ai_action_v2",
        lambda: bool(
            (
                lambda f: (
                    len(f) == 2
                    and f[0][:19]
                    == bytes(
                        [
                            0xA3,
                            0x00,
                            0x01,
                            0x02,
                            0x58,
                            0x01,
                            0x0E,
                            0x00,
                            0x02,
                            0x01,
                            0x04,
                            0x03,
                            0x05,
                            0x00,
                            0x01,
                            0xFF,
                            0x00,
                            0x80,
                            0x0A,
                        ]
                    )
                    and f[1][:5] == bytes([0xA3, 0xFF, 0x14, 0x07, 0x32])
                    and f[1][5:19] == bytes(14)
                )
            )(build_set_ai_action_v2(True, 0x0102, 0x0304, 5, [0xFF0080], 10, 20, 7, 50))
            and build_set_ai_action_v2(False, 0, 0, 0, [], 0, 0, 0, 50)[0][5:8] == bytes([0x00, 0x0B, 0x00])
        ),
    ),
    _check(
        "build_shape_layout_h6067",
        lambda: bool(
            build_shape_layout_h6067([(1, 0, 0, 60), (2, 100, -50, 0)], 121, ext=(1, 2), states=[1, 0])
            == [
                bytes.fromhex("a3000102310201000000003c00026400ceff00f9"),
                bytes.fromhex("a3ff00010200000100000000000000000000005e"),
            ]
            and build_shape_layout_h6067([(1, 0, 0, 60), (2, 100, -50, 0)], 97)
            == [
                bytes.fromhex("a3000102310201000000003c00026400ceff00f9"),
                bytes.fromhex("a3ff00000000000000000000000000000000005c"),
            ]
        ),
    ),
    _check(
        "build_shape_layout_h606a",
        lambda: bool(
            build_shape_layout_h606a([(0, 0, 60), (100, -50, 0), (-30, 20, 120)])
            == [
                bytes.fromhex("a3000102313c0003000000006400ceffe2ff14f2"),
                bytes.fromhex("a3ff00000000000000000000000000000000005c"),
            ]
            and build_shape_layout_h606a([(0, 0, 60), (100, -50, 0), (-30, 20, 120), (5, 5, 0)], 30)
            == [
                bytes.fromhex("a3000102315a0004000000006400ceffe2ff1493"),
                bytes.fromhex("a3ff00050005000000000000000000000000005c"),
            ]
        ),
    ),
    _check(
        "read_ai_edit_info",
        lambda: bool(
            read_ai_edit_info(True, 0x0102, 0x0304)[:8]
            == bytes([0xAC, 0x01, 0x01, 0x02, 0x01, 0x04, 0x03, 0x00])
            and read_ai_edit_info(False, 0, 0)[:3] == bytes([0xAC, 0x01, 0x00])
            and read_ai_edit_info(True, 0x0102, 0x0304)[19] == 0xAC ^ 0x01 ^ 0x01 ^ 0x02 ^ 0x01 ^ 0x04 ^ 0x03
        ),
    ),
    _check(
        "read_ai_edit_info",
        lambda: bool(
            read_ai_edit_info(True, 0x0102, 0x0304)[:8]
            == bytes([0xAC, 0x01, 0x01, 0x02, 0x01, 0x04, 0x03, 0x00])
            and read_ai_edit_info(False, 0, 0)[:3] == bytes([0xAC, 0x01, 0x00])
            and read_ai_edit_info(True, 0x0102, 0x0304)[19] == 0xAC ^ 0x01 ^ 0x01 ^ 0x02 ^ 0x01 ^ 0x04 ^ 0x03
        ),
    ),
    _check(
        "read_hollowlamp_local_colors",
        lambda: bool(
            read_hollowlamp_local_colors() == bytes.fromhex("aaa501000000000000000000000000000000000e")
            and read_hollowlamp_local_colors(3) == bytes.fromhex("aaa503000000000000000000000000000000000c")
        ),
    ),
    _check(
        "read_hollowlamp_local_colors",
        lambda: bool(
            read_hollowlamp_local_colors() == bytes.fromhex("aaa501000000000000000000000000000000000e")
            and read_hollowlamp_local_colors(3) == bytes.fromhex("aaa503000000000000000000000000000000000c")
            and read_hollowlamp_local_colors(2) == bytes.fromhex("aaa502000000000000000000000000000000000d")
        ),
    ),
    _check(
        "build_mtu_a4_frames",
        lambda: bool(
            build_mtu_a4_frames(0x62, bytes(11))
            == [
                bytes([0xA4, 0x00, 0x00, 0x01, 0x02, 0x00, 0x62] + [0] * 11 + [0xC5]),
                bytes([0xA4, 0xFF, 0xFF, 0xA4]),
            ]
            and len(build_mtu_a4_frames(0x62, bytes(187))) == 12
            and build_mtu_a4_frames(0x62, bytes(187))[0][4:7] == bytes([0x0C, 0x00, 0x62])
            and len(build_mtu_a4_frames(0x62, bytes(187))[0]) == 20
            and [len(f) for f in build_mtu_a4_frames(0x62, bytes(187))][-1] == 19
            and build_mtu_a4_frames(0x58, bytes([0x20]) + bytes(600), 509)[0][:7]
            == bytes([0xA4, 0x00, 0x00, 0x01, 0x02, 0x00, 0x58])
            and [len(f) for f in build_mtu_a4_frames(0x58, bytes([0x20]) + bytes(600), 509)] == [509, 104]
        ),
    ),
    _check(
        "build_remote_pair",
        lambda: bool(build_remote_pair(True) == bytes([0x33, 0xA4, 0x01] + [0] * 16 + [0x33 ^ 0xA4 ^ 0x01])),
    ),
    _check(
        "build_remote_pair",
        lambda: bool(
            build_remote_pair(True)[:4] == bytes([0x33, 0xA4, 0x01, 0x00])
            and build_remote_pair(False)[:3] == bytes([0x33, 0xA4, 0x00])
            and build_remote_pair(True)[19] == 0x33 ^ 0xA4 ^ 0x01
        ),
    ),
    _check(
        "build_remote_pair",
        lambda: bool(
            build_remote_pair(True) == bytes([0x33, 0xA4, 0x01] + [0] * 16 + [0x33 ^ 0xA4 ^ 0x01])
            and build_remote_pair(False)[:3] == bytes([0x33, 0xA4, 0x00])
        ),
    ),
    _check(
        "read_remote_pair", lambda: bool(read_remote_pair() == bytes([0xAA, 0xA4] + [0] * 17 + [0xAA ^ 0xA4]))
    ),
    _check(
        "read_remote_pair", lambda: bool(read_remote_pair() == bytes([0xAA, 0xA4] + [0] * 17 + [0xAA ^ 0xA4]))
    ),
    _check(
        "read_remote_pair", lambda: bool(read_remote_pair() == bytes([0xAA, 0xA4] + [0] * 17 + [0xAA ^ 0xA4]))
    ),
    _check(
        "build_mic_color_cmd_mode",
        lambda: bool(
            build_mic_color_cmd_mode(bytes([0x15, 0x01, 0xFF, 0x00, 0x00]))[:8]
            == bytes([0x33, 0x05, 0x15, 0x01, 0xFF, 0x00, 0x00, 0x00])
            and build_mic_color_cmd_mode(b"") == bytes([0x33, 0x05]) + bytes(17) + bytes([0x36])
            and build_mic_color_cmd_mode(bytes(range(1, 21)))[2:19] == bytes(range(1, 18))
        ),
    ),
    _check(
        "build_mic_color_cmd_over56",
        lambda: bool(
            build_mic_color_cmd_over56(bytes(range(1, 11)))[:16]
            == bytes([0xA3, 0x00, 0x01, 0x02, 0x6E]) + bytes(range(1, 11)) + bytes(1)
            and build_mic_color_cmd_over56(bytes(40))[3] == 3
            and build_mic_color_cmd_over56(bytes(31))[3] == 2
            and build_mic_color_cmd_over56(bytes(32))[3] == 3
            and build_mic_color_cmd_over56(bytes(range(1, 31)))[5:19] == bytes(range(1, 15))
        ),
    ),
    _check(
        "read_barelightv1_group_colors",
        lambda: bool(
            [f[:3] for f in read_barelightv1_group_colors(True, 15)]
            == [bytes([0xAA, 0xA5, g]) for g in range(1, 6)]
            and [f[:3] for f in read_barelightv1_group_colors(False, 15)]
            == [bytes([0xAA, 0xA2, g]) for g in range(1, 5)]
            and len(read_barelightv1_group_colors(False, 17)) == 5
        ),
    ),
    _check(
        "read_h6119_group_colors",
        lambda: bool(
            [f[:3] for f in read_h6119_group_colors(10)] == [bytes([0xAA, 0xA5, g]) for g in range(1, 5)]
            and all(f[3:19] == bytes(16) and f[19] == 0xAA ^ 0xA5 ^ f[2] for f in read_h6119_group_colors(10))
            and len(read_h6119_group_colors(9)) == 3
        ),
    ),
    _check(
        "read_h612526_group_colors",
        lambda: bool(
            [f[:3] for f in read_h612526_group_colors(False)] == [bytes([0xAA, 0xA2, g]) for g in range(1, 5)]
            and [f[:3] for f in read_h612526_group_colors(True)]
            == [bytes([0xAA, 0xA5, g]) for g in range(1, 6)]
            and all(
                f[3:19] == bytes(16) and f[19] == f[0] ^ f[1] ^ f[2]
                for f in read_h612526_group_colors(True) + read_h612526_group_colors(False)
            )
        ),
    ),
    _check(
        "read_hollowlamp_local_color_count",
        lambda: bool(
            read_hollowlamp_local_color_count() == bytes.fromhex("aaa500000000000000000000000000000000000f")
        ),
    ),
    _check(
        "read_rgbic_group_colors",
        lambda: bool(
            [f[:3] for f in read_rgbic_group_colors(False, 4, 15)]
            == [bytes([0xAA, 0xA2, g]) for g in range(1, 5)]
            and [f[:3] for f in read_rgbic_group_colors(True, 3, 15)]
            == [bytes([0xAA, 0xA5, g]) for g in range(1, 6)]
            and len(read_rgbic_group_colors(True, 4, 15)) == 4
            and all(
                f[3:19] == bytes(16) and f[19] == f[0] ^ f[1] ^ f[2]
                for f in read_rgbic_group_colors(True, 3, 16)
            )
        ),
    ),
    _check(
        "build_auto_induction",
        lambda: bool(
            build_auto_induction(True)[:4] == bytes([0x33, 0xA6, 0x01, 0x00])
            and build_auto_induction(False)[:3] == bytes([0x33, 0xA6, 0x00])
        ),
    ),
    _check(
        "build_auto_induction",
        lambda: bool(
            build_auto_induction(True) == bytes([0x33, 0xA6, 0x01] + [0] * 16 + [0x33 ^ 0xA6 ^ 0x01])
            and build_auto_induction(False)[:3] == bytes([0x33, 0xA6, 0x00])
        ),
    ),
    _check(
        "build_exit_remote_pair",
        lambda: bool(
            build_exit_remote_pair()[:3] == bytes([0x33, 0xA6, 0x00])
            and build_exit_remote_pair()[19] == (0x33 ^ 0xA6)
        ),
    ),
    _check(
        "build_exit_remote_pair",
        lambda: bool(build_exit_remote_pair() == bytes([0x33, 0xA6] + [0] * 17 + [0x33 ^ 0xA6])),
    ),
    _check(
        "build_mtu_a6_package",
        lambda: bool(
            build_mtu_a6_package(bytes([3, 0x7F, 1, 1]), 100, 51, bytes(range(8)))
            == [
                bytes.fromhex("a600000101010001020304050607020063037f0101ba"),
                bytes([0xA6, 0xFF, 0xFF, 0xA6]),
            ]
            and [len(f) for f in build_mtu_a6_package(bytes(range(40)), 20, 51, bytes(range(8)), 2, 2)]
            == [20, 20, 20, 10]
            and build_mtu_a6_package(bytes(range(40)), 20, 51, bytes(range(8)), 2, 2)[0][:18]
            == bytes([0xA6, 0, 0, 1, 2, 2, 0, 1, 2, 3, 4, 5, 6, 7, 4, 0, 0x63, 0])
            and build_mtu_a6_package(bytes(range(40)), 20, 51, bytes(range(8)), 2, 2)[1][:3]
            == bytes([0xA6, 0x01, 0x00])
            and build_mtu_a6_package(bytes(range(40)), 20, 51, bytes(range(8)), 2, 2)[-1]
            == bytes.fromhex("a6ffff222324252627a7")
        ),
    ),
    _check(
        "build_position_calibration_h6062",
        lambda: bool(
            build_position_calibration_h6062(False)[:5] == bytes([0x33, 0xA6, 0x01, 0x01, 0x00])
            and build_position_calibration_h6062(True)[:4] == bytes([0x33, 0xA6, 0x01, 0x00])
        ),
    ),
    _check(
        "build_position_calibration_h6062",
        lambda: bool(
            build_position_calibration_h6062(True)[:4] == bytes([0x33, 0xA6, 0x01, 0x00])
            and build_position_calibration_h6062(False)[:4] == bytes([0x33, 0xA6, 0x01, 0x01])
            and build_position_calibration_h6062(True)[19] == 0x94
        ),
    ),
    _check(
        "build_position_calibration_h6062",
        lambda: bool(
            build_position_calibration_h6062(False)[:5] == bytes([0x33, 0xA6, 0x01, 0x01, 0x00])
            and build_position_calibration_h6062(True)[:4] == bytes([0x33, 0xA6, 0x01, 0x00])
            and build_position_calibration_h6062(True)[19] == (0x33 ^ 0xA6 ^ 0x01)
        ),
    ),
    _check("read_logo", lambda: bool(read_logo() == bytes([0xAA, 0xA6] + [0] * 17 + [0xAA ^ 0xA6]))),
    _check(
        "read_logo",
        lambda: bool(read_logo() == append_bcc(bytes([0xAA, 0xA6])) and read_logo()[2:19] == bytes(17)),
    ),
    _check("read_logo", lambda: bool(read_logo() == bytes([0xAA, 0xA6] + [0] * 17 + [0xAA ^ 0xA6]))),
    _check(
        "read_logo_or_auto_induction",
        lambda: bool(
            read_logo_or_auto_induction()[:3] == bytes([0xAA, 0xA6, 0x00])
            and read_logo_or_auto_induction()[19] == (0xAA ^ 0xA6)
        ),
    ),
    _check(
        "read_position_calibration_h6062",
        lambda: bool(
            read_position_calibration_h6062()[:4] == bytes([0xAA, 0xA6, 0x01, 0x00])
            and read_position_calibration_h6062()[19] == (0xAA ^ 0xA6 ^ 0x01)
        ),
    ),
    _check(
        "read_position_calibration_h6062",
        lambda: bool(
            read_position_calibration_h6062()[:4] == bytes([0xAA, 0xA6, 0x01, 0x00])
            and read_position_calibration_h6062()[19] == 0x0D
        ),
    ),
    _check(
        "read_position_calibration_h6062",
        lambda: bool(
            read_position_calibration_h6062() == bytes([0xAA, 0xA6, 0x01] + [0] * 16 + [0xAA ^ 0xA6 ^ 0x01])
        ),
    ),
    _check(
        "build_hollowlamp_sensitivity",
        lambda: bool(
            build_hollowlamp_sensitivity(15) == bytes([0x33, 0xA7, 0x0F] + [0] * 16 + [0x33 ^ 0xA7 ^ 0x0F])
        ),
    ),
    _check(
        "build_sensitivity",
        lambda: bool(build_sensitivity(5) == bytes([0x33, 0xA7, 0x05] + [0] * 16 + [0x33 ^ 0xA7 ^ 0x05])),
    ),
    _check(
        "build_sensitivity",
        lambda: bool(build_sensitivity(5) == bytes([0x33, 0xA7, 0x05] + [0] * 16 + [0x33 ^ 0xA7 ^ 0x05])),
    ),
    _check(
        "read_hollowlamp_sensitivity",
        lambda: bool(read_hollowlamp_sensitivity() == bytes([0xAA, 0xA7] + [0] * 17 + [0xAA ^ 0xA7])),
    ),
    _check(
        "read_sensitivity", lambda: bool(read_sensitivity() == bytes([0xAA, 0xA7] + [0] * 17 + [0xAA ^ 0xA7]))
    ),
    _check(
        "read_sensitivity", lambda: bool(read_sensitivity() == bytes([0xAA, 0xA7] + [0] * 17 + [0xAA ^ 0xA7]))
    ),
    _check(
        "build_ai_filter",
        lambda: bool(
            build_ai_filter(True, 50, 50, 50, 50, 0x0102, 0x0304, 2026, 10, 15, 8, 30)[:19]
            == bytes(
                [
                    0x33,
                    0xA9,
                    0x10,
                    0x0F,
                    0x01,
                    50,
                    50,
                    50,
                    50,
                    0x02,
                    0x01,
                    0x04,
                    0x03,
                    0xEA,
                    0x07,
                    10,
                    15,
                    8,
                    30,
                ]
            )
        ),
    ),
    _check(
        "build_ai_identify_model",
        lambda: bool(
            build_ai_identify_model(0x0102)[:7] == bytes([0x33, 0xA9, 0x08, 0x02, 0x02, 0x01, 0x00])
        ),
    ),
    _check(
        "build_ai_switch",
        lambda: bool(
            build_ai_switch(True)[:6] == bytes([0x33, 0xA9, 0x04, 0x01, 0x01, 0x00])
            and build_ai_switch(False)[4] == 0x00
        ),
    ),
    _check(
        "build_black_border_remove",
        lambda: bool(build_black_border_remove(True)[:6] == bytes([0x33, 0xA9, 0x0B, 0x01, 0x01, 0x00])),
    ),
    _check(
        "build_black_screen",
        lambda: bool(
            build_black_screen(True, True, 10, 300)[:11]
            == bytes([0x33, 0xA9, 0x0A, 0x06, 0x01, 0x01, 0x0A, 0x00, 0x2C, 0x01, 0x00])
            and build_black_screen(True, False, 10, 300)[5] == 0x02
        ),
    ),
    _check(
        "build_black_screen_hdmi",
        lambda: bool(
            build_black_screen_hdmi(True, 300)[:8] == bytes([0x33, 0xA9, 0x0C, 0x03, 0x01, 0x2C, 0x01, 0x00])
        ),
    ),
    _check(
        "build_hdmi_port", lambda: bool(build_hdmi_port(2)[:6] == bytes([0x33, 0xA9, 0x03, 0x01, 0x02, 0x00]))
    ),
    _check(
        "build_hdr_cali",
        lambda: bool(
            build_hdr_cali()[:4] == bytes([0x33, 0xA9, 0x0D, 0x00])
            and build_hdr_cali()[19] == 0x33 ^ 0xA9 ^ 0x0D
        ),
    ),
    _check(
        "build_video_auto_wb",
        lambda: bool(build_video_auto_wb(1)[:6] == bytes([0x33, 0xA9, 0x12, 0x01, 0x01, 0x00])),
    ),
    _check(
        "build_video_auto_wb_old",
        lambda: bool(
            build_video_auto_wb_old()[:4] == bytes([0x33, 0xA9, 0x12, 0x00])
            and build_video_auto_wb_old()[19] == 0x33 ^ 0xA9 ^ 0x12
        ),
    ),
    _check(
        "build_video_brightness",
        lambda: bool(build_video_brightness(50)[:6] == bytes([0x33, 0xA9, 0x02, 0x01, 50, 0x00])),
    ),
    _check(
        "build_video_color_cali",
        lambda: bool(
            build_video_color_cali(1, 2, 3)[:8] == bytes([0x33, 0xA9, 0x05, 0x03, 1, 2, 3, 0])
            and build_video_color_cali(1, 2, 3, 4)[:9] == bytes([0x33, 0xA9, 0x05, 0x04, 1, 2, 3, 4, 0])
        ),
    ),
    _check(
        "build_video_hdr_effect",
        lambda: bool(build_video_hdr_effect(True, 40)[:7] == bytes([0x33, 0xA9, 0x11, 0x02, 0x01, 40, 0x00])),
    ),
    _check(
        "build_video_saturation",
        lambda: bool(build_video_saturation(70)[:6] == bytes([0x33, 0xA9, 0x0E, 0x01, 70, 0x00])),
    ),
    _check(
        "build_video_sensitivity",
        lambda: bool(build_video_sensitivity(60)[:6] == bytes([0x33, 0xA9, 0x01, 0x01, 60, 0x00])),
    ),
    _check(
        "build_video_wb_cali_exit",
        lambda: bool(build_video_wb_cali_exit()[:4] == bytes([0x33, 0xA9, 0x14, 0x00])),
    ),
    _check(
        "build_video_white_balance",
        lambda: bool(
            build_video_white_balance(False, 40, 60)[:8]
            == bytes([0x33, 0xA9, 0x00, 0x03, 0x01, 40, 60, 0x00])
            and build_video_white_balance(True, 0, 0)[4] == 0x00
        ),
    ),
    _check(
        "build_video_white_balance_advanced",
        lambda: bool(
            build_video_white_balance_advanced(10, 20, 30)[:8]
            == bytes([0x33, 0xA9, 0x0F, 0x03, 10, 20, 30, 0x00])
        ),
    ),
    _check(
        "build_video_white_balance_v1",
        lambda: bool(build_video_white_balance_v1(50)[:6] == bytes([0x33, 0xA9, 0x06, 0x01, 50, 0x00])),
    ),
    _check("read_ai_filter", lambda: bool(read_ai_filter()[:4] == bytes([0xAA, 0xA9, 0x10, 0x00]))),
    _check(
        "read_black_border_remove",
        lambda: bool(read_black_border_remove()[:4] == bytes([0xAA, 0xA9, 0x0B, 0x00])),
    ),
    _check("read_black_screen", lambda: bool(read_black_screen()[:4] == bytes([0xAA, 0xA9, 0x0A, 0x00]))),
    _check(
        "read_black_screen_hdmi",
        lambda: bool(read_black_screen_hdmi()[:4] == bytes([0xAA, 0xA9, 0x0C, 0x00])),
    ),
    _check("read_hdr_cali", lambda: bool(read_hdr_cali()[:4] == bytes([0xAA, 0xA9, 0x0D, 0x00]))),
    _check(
        "read_video_brightness", lambda: bool(read_video_brightness()[:4] == bytes([0xAA, 0xA9, 0x02, 0x00]))
    ),
    _check(
        "read_video_check_wb_cali",
        lambda: bool(read_video_check_wb_cali()[:4] == bytes([0xAA, 0xA9, 0x13, 0x00])),
    ),
    _check(
        "read_video_color_cali", lambda: bool(read_video_color_cali()[:4] == bytes([0xAA, 0xA9, 0x05, 0x00]))
    ),
    _check(
        "read_video_hdr_effect", lambda: bool(read_video_hdr_effect()[:4] == bytes([0xAA, 0xA9, 0x11, 0x00]))
    ),
    _check(
        "read_video_saturation", lambda: bool(read_video_saturation()[:4] == bytes([0xAA, 0xA9, 0x0E, 0x00]))
    ),
    _check(
        "read_video_sensitivity",
        lambda: bool(read_video_sensitivity()[:4] == bytes([0xAA, 0xA9, 0x01, 0x00])),
    ),
    _check(
        "read_video_white_balance",
        lambda: bool(
            read_video_white_balance()[:4] == bytes([0xAA, 0xA9, 0x00, 0x00])
            and read_video_white_balance()[19] == 0xAA ^ 0xA9
        ),
    ),
    _check(
        "read_video_white_balance_v1",
        lambda: bool(read_video_white_balance_v1()[:4] == bytes([0xAA, 0xA9, 0x06, 0x00])),
    ),
    _check(
        "build_check_light_install",
        lambda: bool(build_check_light_install() == bytes([0x33, 0xAA] + [0] * 17 + [0x99])),
    ),
    _check(
        "build_ota_pact_report",
        lambda: bool(
            build_ota_pact_report(0x0102, 0x03) == bytes([0xAB, 0x02, 0x02, 0x01, 0x03] + [0] * 14 + [0xA9])
        ),
    ),
    _check(
        "build_plug_ota_pact_report",
        lambda: bool(
            build_plug_ota_pact_report(2, 2) == bytes([0xAB, 0x02, 0x02, 0x00, 0x02] + [0] * 14 + [0xA9])
            and build_plug_ota_pact_report(2, 1) == bytes([0xAB, 0x02, 0x02, 0x00, 0x01] + [0] * 14 + [0xAA])
            and build_plug_ota_pact_report(0x0102, 3)[2:5] == bytes([0x02, 0x01, 0x03])
        ),
    ),
    _check(
        "read_dynamic_api_support",
        lambda: bool(read_dynamic_api_support() == bytes([0xAA, 0xAB] + [0] * 17 + [0x01])),
    ),
    _check(
        "read_wifi_func_list", lambda: bool(read_wifi_func_list() == bytes([0xAB, 0x03] + [0] * 17 + [0xA8]))
    ),
    _check(
        "read_wifi_func_list", lambda: bool(read_wifi_func_list() == bytes([0xAB, 0x03] + [0] * 17 + [0xA8]))
    ),
    _check(
        "build_pick_mic",
        lambda: bool(
            build_pick_mic(2)[:4] == bytes([0x33, 0xAC, 0x02, 0x00])
            and build_pick_mic(2)[19] == (0x33 ^ 0xAC ^ 0x02)
        ),
    ),
    _check(
        "build_pick_mic",
        lambda: bool(
            build_pick_mic(2)[:4] == bytes([0x33, 0xAC, 0x02, 0x00])
            and build_pick_mic(2)[19] == (0x33 ^ 0xAC ^ 0x02)
        ),
    ),
    _check(
        "build_pick_mic",
        lambda: bool(
            build_pick_mic(1) == bytes.fromhex("33ac01" + "00" * 16 + "9e")
            and build_pick_mic(0)[:3] == bytes([0x33, 0xAC, 0x00])
        ),
    ),
    _check(
        "read_bluetooth_name", lambda: bool(read_bluetooth_name() == bytes([0xAC, 0x06] + [0] * 17 + [0xAA]))
    ),
    _check(
        "read_bluetooth_name", lambda: bool(read_bluetooth_name() == bytes([0xAC, 0x06] + [0] * 17 + [0xAA]))
    ),
    _check("read_combo_state", lambda: bool(read_combo_state() == bytes([0xAC, 0x07] + [0] * 17 + [0xAB]))),
    _check(
        "read_cube_device_self",
        lambda: bool(
            read_cube_device_self()[:4] == bytes([0xAC, 0x02, 0x00, 0x00])
            and read_cube_device_self()[19] == 0xAE
        ),
    ),
    _check("read_detail_info", lambda: bool(read_detail_info() == bytes([0xAC, 0x03] + [0] * 17 + [0xAF]))),
    _check(
        "read_h5086_recent_hour_chart",
        lambda: bool(
            read_h5086_recent_hour_chart()[:4] == bytes([0xAC, 0x01, 0x0D, 0x00])
            and read_h5086_recent_hour_chart()[19] == 0xAC ^ 0x01 ^ 0x0D
        ),
    ),
    _check(
        "read_h5086_timers",
        lambda: bool(
            read_h5086_timers()[:4] == bytes([0xAC, 0x01, 0x13, 0x00])
            and read_h5086_timers()[19] == 0xAC ^ 0x01 ^ 0x13
        ),
    ),
    _check(
        "read_h6047_sound_way",
        lambda: bool(read_h6047_sound_way() == bytes([0xAA, 0xAC] + [0] * 17 + [0xAA ^ 0xAC])),
    ),
    _check(
        "read_h6047_sound_way",
        lambda: bool(read_h6047_sound_way() == bytes([0xAA, 0xAC] + [0] * 17 + [0xAA ^ 0xAC])),
    ),
    _check(
        "read_sound_pickup",
        lambda: bool(read_sound_pickup() == bytes([0xAA, 0xAC] + [0] * 17 + [0x06]) == read_request(0xAC)),
    ),
    _check(
        "read_sound_pickup", lambda: bool(read_sound_pickup() == bytes.fromhex("aaac" + "00" * 17 + "06"))
    ),
    _check(
        "read_status_color_h667172",
        lambda: bool(
            read_status_color_h667172() == bytes.fromhex("ac03034041a50000000000000000000000000008")
        ),
    ),
    _check(
        "read_status_color_h703ab",
        lambda: bool(read_status_color_h703ab() == bytes.fromhex("ac030441a5401600000000000000000000000019")),
    ),
    _check(
        "read_status_color_rgbic_351",
        lambda: bool(
            read_status_color_rgbic_351() == bytes.fromhex("ac030240a5000000000000000000000000000048")
        ),
    ),
    _check(
        "read_status_h1771",
        lambda: bool(read_status_h1771() == bytes.fromhex("ac03044136a3a5" + "00" * 12 + "da")),
    ),
    _check(
        "read_status_h61d3_263",
        lambda: bool(read_status_h61d3_263() == bytes.fromhex("ac030441a54016" + "00" * 12 + "19")),
    ),
    _check(
        "read_status_straightfloorlamp",
        lambda: bool(read_status_straightfloorlamp() == bytes.fromhex("ac0302a5a3" + "00" * 14 + "ab")),
    ),
    _check(
        "read_relative_brightness",
        lambda: bool(
            read_relative_brightness() == bytes([0xAA, 0xAE, 0x01] + [0] * 16 + [0xAA ^ 0xAE ^ 0x01])
        ),
    ),
    _check(
        "read_relative_brightness",
        lambda: bool(
            read_relative_brightness() == bytes([0xAA, 0xAE, 0x01] + [0] * 16 + [0xAA ^ 0xAE ^ 0x01])
        ),
    ),
    _check(
        "read_relative_brightness",
        lambda: bool(
            read_relative_brightness() == bytes([0xAA, 0xAE, 0x01] + [0] * 16 + [0xAA ^ 0xAE ^ 0x01])
        ),
    ),
    _check(
        "build_exit_check_light_install",
        lambda: bool(build_exit_check_light_install() == bytes([0x33, 0xAF] + [0] * 17 + [0x9C])),
    ),
    _check(
        "build_exit_checking_install",
        lambda: bool(build_exit_checking_install() == bytes([0x33, 0xAF] + [0] * 17 + [0x33 ^ 0xAF])),
    ),
    _check(
        "build_plug_delay",
        lambda: bool(
            build_plug_delay(0, 1, 2 * 60 + 30)[:7] == bytes([0x33, 0xB0, 0x00, 0x01, 0x02, 0x1E, 0x00])
            and build_plug_delay(1, 0, 45)[:6] == bytes([0x33, 0xB0, 0x01, 0x00, 0x00, 0x2D])
            and all(
                build_plug_delay(0, t, h * 60 + m)[:6] == bytes([0x33, 0xB0, 0x00, t, h, m])
                for t in (0, 1)
                for h in range(24)
                for m in range(60)
            )
        ),
    ),
    _check(
        "build_plug_delay",
        lambda: bool(
            build_plug_delay(1, 0, 125)[:7] == bytes([0x33, 0xB0, 0x01, 0x00, 0x02, 0x05, 0x00])
            and build_plug_delay(0, 1, 59)[:6] == bytes([0x33, 0xB0, 0x00, 0x01, 0x00, 0x3B])
        ),
    ),
    _check(
        "build_plug_delay",
        lambda: bool(
            build_plug_delay(0, 1, 2 * 60 + 30)[:7] == bytes([0x33, 0xB0, 0x00, 0x01, 0x02, 0x1E, 0x00])
            and build_plug_delay(0, 0, 45)[:6] == bytes([0x33, 0xB0, 0x00, 0x00, 0x00, 0x2D])
        ),
    ),
    _check(
        "build_plug_pro_delay",
        lambda: bool(
            build_plug_pro_delay(True, 2, 30)[:6] == bytes([0x33, 0xB0, 0x01, 0x02, 0x1E, 0x00])
            and build_plug_pro_delay(False, 0, 5)[:5] == bytes([0x33, 0xB0, 0x00, 0x00, 0x05])
        ),
    ),
    _check(
        "read_plug_delay",
        lambda: bool(
            read_plug_delay(0, 1)[:5] == bytes([0xAA, 0xB0, 0x00, 0x01, 0x00])
            and read_plug_delay(2, 0)[:4] == bytes([0xAA, 0xB0, 0x02, 0x00])
        ),
    ),
    _check(
        "read_plug_delay",
        lambda: bool(
            read_plug_delay(0, 0)[:5] == bytes([0xAA, 0xB0, 0x00, 0x00, 0x00])
            and read_plug_delay(0, 1)[:5] == bytes([0xAA, 0xB0, 0x00, 0x01, 0x00])
        ),
    ),
    _check(
        "read_plug_delay",
        lambda: bool(
            read_plug_delay(0, 0) == bytes([0xAA, 0xB0, 0x00, 0x00] + [0x00] * 15 + [0xAA ^ 0xB0])
            and read_plug_delay(0, 1)[:5] == bytes([0xAA, 0xB0, 0x00, 0x01, 0x00])
            and read_plug_delay(1, 1)[19] == 0xAA ^ 0xB0 ^ 0x01 ^ 0x01
            and read_plug_delay(2, 1)[:5] == bytes([0xAA, 0xB0, 0x02, 0x01, 0x00])
            and read_plug_delay(2, 1)[19] == 0xAA ^ 0xB0 ^ 0x02 ^ 0x01
        ),
    ),
    _check(
        "read_plug_pro_delay",
        lambda: bool(
            read_plug_pro_delay(False)[:4] == bytes([0xAA, 0xB0, 0x00, 0x00])
            and read_plug_pro_delay(True)[:4] == bytes([0xAA, 0xB0, 0x01, 0x00])
        ),
    ),
    _check(
        "build_secret_write",
        lambda: bool(
            build_secret_write(bytes(range(1, 9)))
            == bytes([0x33, 0xB2]) + bytes(range(1, 9)) + bytes(9) + bytes([0x33 ^ 0xB2 ^ 0x08])
            and build_secret_write(b"") == bytes([0x33, 0xB2]) + bytes(17) + bytes([0x81])
            and _raises(ValueError, lambda: build_secret_write(bytes(7)))
            and _raises(ValueError, lambda: build_secret_write(bytes(9)))
            and _raises(ValueError, lambda: build_secret_write(bytes(17)))
        ),
    ),
    _check("read_secret", lambda: bool(read_secret() == bytes([0xAA, 0xB1]) + bytes(17) + bytes([0x1B]))),
    _check("read_plug_spec", lambda: bool(read_plug_spec() == bytes([0xAA, 0xB3] + [0x00] * 17 + [0x19]))),
    _check("read_plug_spec", lambda: bool(read_plug_spec() == bytes([0xAA, 0xB3] + [0x00] * 17 + [0x19]))),
    _check(
        "read_plug_spec",
        lambda: bool(read_plug_spec()[:3] == bytes([0xAA, 0xB3, 0x00]) and read_plug_spec()[19] == 0x19),
    ),
    _check(
        "build_plug_pro_timer",
        lambda: bool(
            build_plug_pro_timer(1, True, True, 6, 45, 0x7F)[:8]
            == bytes([0x33, 0xB4, 0x01, 0x81, 0x06, 0x2D, 0x7F, 0x00])
            and build_plug_pro_timer(2, False, False, 23, 0, 0xFF)[2:7]
            == bytes([0x02, 0x00, 0x17, 0x00, 0xFF])
        ),
    ),
    _check(
        "build_plug_timer",
        lambda: bool(
            build_plug_timer(2, 1, True, True, 7, 30, 0x7F)[:8]
            == bytes([0x33, 0xB4, 0x12, 0x81, 0x07, 0x1E, 0x7F, 0x00])
            and build_plug_timer(3, 0, True, False, 23, 59, 0xFF)[:8]
            == bytes([0x33, 0xB4, 0x03, 0x80, 0x17, 0x3B, 0xFF, 0x00])
            and build_plug_timer(1, 2, False, True, 6, 0, 0x00)[2:4] == bytes([0x11, 0x01])
        ),
    ),
    _check(
        "build_plug_timer",
        lambda: bool(
            build_plug_timer(2, 1, True, True, 7, 30, 0x7F)[:8]
            == bytes([0x33, 0xB4, 0x12, 0x81, 0x07, 0x1E, 0x7F, 0x00])
            and build_plug_timer(3, 0, True, False, 23, 59, 0xFF)[:8]
            == bytes([0x33, 0xB4, 0x03, 0x80, 0x17, 0x3B, 0xFF, 0x00])
        ),
    ),
    _check(
        "build_plug_timer",
        lambda: bool(
            build_plug_timer(2, 1, True, True, 7, 30, 0x7F)[:8]
            == bytes([0x33, 0xB4, 0x12, 0x81, 0x07, 0x1E, 0x7F, 0x00])
            and build_plug_timer(3, 0, True, False, 23, 59, 0xFF)[:8]
            == bytes([0x33, 0xB4, 0x03, 0x80, 0x17, 0x3B, 0xFF, 0x00])
            and build_plug_timer(1, 0, True, True, 6, 0, 0x7F)[:7]
            == bytes([0x33, 0xB4, 0x01, 0x81, 0x06, 0x00, 0x7F])
        ),
    ),
    _check(
        "read_plug_timers",
        lambda: bool(
            read_plug_timers(0)[:5] == bytes([0xAA, 0xB4, 0x00, 0xFF, 0x00])
            and read_plug_timers(1)[:4] == bytes([0xAA, 0xB4, 0x01, 0xFF])
            and read_plug_timers(2)[2] == 0x00
            and read_plug_timers(0)[19] == 0xAA ^ 0xB4 ^ 0xFF
        ),
    ),
    _check(
        "read_plug_timers",
        lambda: bool(
            read_plug_timers(1)[:5] == bytes([0xAA, 0xB4, 0x01, 0xFF, 0x00])
            and read_plug_timers(0)[:4] == bytes([0xAA, 0xB4, 0x00, 0xFF])
            and read_plug_timers(2)[2] == 0x00
        ),
    ),
    _check(
        "read_plug_timers", lambda: bool(read_plug_timers(0)[:5] == bytes([0xAA, 0xB4, 0x00, 0xFF, 0x00]))
    ),
    _check(
        "read_plug_timers",
        lambda: bool(
            read_plug_timers(1)[:5] == bytes([0xAA, 0xB4, 0x01, 0xFF, 0x00])
            and read_plug_timers(0)[:4] == bytes([0xAA, 0xB4, 0x00, 0xFF])
            and read_plug_timers(2)[2] == 0x00
        ),
    ),
    _check(
        "build_plug_sync_time",
        lambda: bool(
            build_plug_sync_time(0x01020304, 5, 30)
            == bytes([0x33, 0xB5, 0x01, 0x02, 0x03, 0x04, 0x01, 0x05, 0x1E] + [0] * 10 + [0x98])
            and build_plug_sync_time(0x01020304, -3, -30)[7:9] == bytes([0xFD, 0xE2])
            and build_plug_sync_time(0x6527A6C0, 9, 0)[2:9]
            == bytes([0x65, 0x27, 0xA6, 0xC0, 0x01, 0x09, 0x00])
        ),
    ),
    _check(
        "build_stop_ai_preview",
        lambda: bool(build_stop_ai_preview() == bytes.fromhex("33b80100" + "00" * 15 + "8a")),
    ),
    _check(
        "build_stop_preview_ai_action",
        lambda: bool(
            build_stop_preview_ai_action()
            == bytes([0x33, 0xB8, 0x01, 0x00] + [0] * 15 + [0x33 ^ 0xB8 ^ 0x01])
        ),
    ),
    _check(
        "build_stop_preview_ai_action",
        lambda: bool(
            build_stop_preview_ai_action()
            == bytes([0x33, 0xB8, 0x01, 0x00] + [0] * 15 + [0x33 ^ 0xB8 ^ 0x01])
        ),
    ),
    _check(
        "read_device_graphics",
        lambda: bool(read_device_graphics() == bytes([0xAA, 0xB9] + [0] * 17 + [0xAA ^ 0xB9])),
    ),
    _check(
        "read_device_graphics",
        lambda: bool(read_device_graphics() == bytes([0xAA, 0xB9] + [0] * 17 + [0xAA ^ 0xB9])),
    ),
    _check(
        "read_device_graphics_md5",
        lambda: bool(read_device_graphics_md5() == bytes.fromhex("aab9" + "00" * 17 + "13")),
    ),
    _check(
        "build_pair_process_complete",
        lambda: bool(build_pair_process_complete() == bytes.fromhex("3aba02" + "00" * 16 + "82")),
    ),
    _check(
        "read_low_blue_light",
        lambda: bool(
            read_low_blue_light() == bytes([0xAA, 0xBA, 0x01] + [0] * 16 + [0xAA ^ 0xBA ^ 0x01])
            and read_low_blue_light(False)[:3] == bytes([0xAA, 0xBA, 0x00])
        ),
    ),
    _check(
        "read_low_blue_light",
        lambda: bool(
            read_low_blue_light() == bytes.fromhex("aaba01" + "00" * 16 + "11")
            and read_low_blue_light(0)[:3] == bytes([0xAA, 0xBA, 0x00])
        ),
    ),
    _check(
        "read_low_blue_light",
        lambda: bool(
            read_low_blue_light() == bytes([0xAA, 0xBA, 0x01] + [0] * 16 + [0xAA ^ 0xBA ^ 0x01])
            and read_low_blue_light(False)[:3] == bytes([0xAA, 0xBA, 0x00])
        ),
    ),
    _check(
        "build_recognition_start",
        lambda: bool(
            build_recognition_start(280)
            == bytes([0x33, 0xED, 0x00, 0x18, 0x01] + [0] * 14 + [0x33 ^ 0xED ^ 0x18 ^ 0x01])
        ),
    ),
    _check(
        "build_recognition_start",
        lambda: bool(build_recognition_start(280)[:6] == bytes([0x33, 0xED, 0x00, 0x18, 0x01, 0x00])),
    ),
    _check(
        "build_recognition_stop",
        lambda: bool(build_recognition_stop() == bytes([0x33, 0xED, 0x01] + [0] * 16 + [0x33 ^ 0xED ^ 0x01])),
    ),
    _check(
        "build_recognition_stop",
        lambda: bool(build_recognition_stop()[:4] == bytes([0x33, 0xED, 0x01, 0x00])),
    ),
    _check(
        "build_video_recognition_effect",
        lambda: bool(
            build_video_recognition_effect(280)[:6] == bytes([0x33, 0xED, 0x00, 0x18, 0x01, 0x00])
            and build_video_recognition_effect(280)[19] == 0x33 ^ 0xED ^ 0x18 ^ 0x01
        ),
    ),
    _check(
        "build_video_recognition_effect",
        lambda: bool(build_video_recognition_effect(280)[:6] == bytes([0x33, 0xED, 0x00, 0x18, 0x01, 0x00])),
    ),
    _check(
        "build_video_recognition_stop",
        lambda: bool(
            build_video_recognition_stop() == bytes([0x33, 0xED, 0x01] + [0x00] * 16 + [0x33 ^ 0xED ^ 0x01])
        ),
    ),
    _check(
        "build_video_recognition_stop",
        lambda: bool(
            build_video_recognition_stop()[:4] == bytes([0x33, 0xED, 0x01, 0x00])
            and build_video_recognition_stop()[19] == 0x33 ^ 0xED ^ 0x01
        ),
    ),
    _check(
        "build_ota_prepare", lambda: bool(build_ota_prepare() == bytes([0x33, 0xEE] + [0x00] * 17 + [0xDD]))
    ),
    _check(
        "build_ota_prepare",
        lambda: bool(
            build_ota_prepare() == append_bcc(bytes([0x33, 0xEE]))
            and build_ota_prepare()[:3] == bytes([0x33, 0xEE, 0x00])
            and build_ota_prepare()[19] == 0xDD
        ),
    ),
    _check("build_ota_prepare", lambda: bool(build_ota_prepare() == bytes([0x33, 0xEE] + [0] * 17 + [0xDD]))),
    _check("read_pact", lambda: bool(read_pact() == bytes([0xAA, 0xEF] + [0] * 17 + [0x45]))),
    _check(
        "read_pact", lambda: bool(read_pact() == bytes.fromhex("aaef000000000000000000000000000000000045"))
    ),
    _check("read_pact", lambda: bool(read_pact() == bytes([0xAA, 0xEF] + [0x00] * 17 + [0x45]))),
    _check("read_pact", lambda: bool(read_pact() == bytes([0xAA, 0xEF] + [0] * 17 + [0x45]))),
    _check(
        "read_pact", lambda: bool(read_pact() == bytes.fromhex("aaef000000000000000000000000000000000045"))
    ),
    _check(
        "build_daysync_after_bind",
        lambda: bool(build_daysync_after_bind() == bytes.fromhex("3305f0ff00000000000000000000000000000039")),
    ),
    _check(
        "build_daysync_after_bind",
        lambda: bool(
            build_daysync_after_bind()[:5] == bytes([0x33, 0x05, 0xF0, 0xFF, 0x00])
            and build_daysync_after_bind()[19] == 0x33 ^ 0x05 ^ 0xF0 ^ 0xFF
        ),
    ),
    _check(
        "build_daysync_after_bind",
        lambda: bool(
            build_daysync_after_bind()[:5] == bytes([0x33, 0x05, 0xF0, 0xFF, 0x00])
            and build_daysync_after_bind()[19] == 0x33 ^ 0x05 ^ 0xF0 ^ 0xFF
        ),
    ),
    _check(
        "build_daysync_mode",
        lambda: bool(
            build_daysync_mode(1, enable_type=1)[:8]
            == bytes([0x33, 0x05, 0xF0, 0x01, 0xFF, 0x01, 0x1E, 0x00])
            and build_daysync_mode(4, scheme_id=3, repeat_frequency=0x7F)[:6]
            == bytes([0x33, 0x05, 0xF0, 0x04, 0x03, 0x7F])
            and build_daysync_mode(0)[:4] == bytes([0x33, 0x05, 0xF0, 0x00])
            and build_daysync_mode(1, enable_type=0)[:7] == bytes([0x33, 0x05, 0xF0, 0x01, 0xFF, 0x00, 0x00])
        ),
    ),
    _check(
        "build_daysync_mode",
        lambda: bool(
            build_daysync_mode(0) == bytes.fromhex("3305f000000000000000000000000000000000c6")
            and build_daysync_mode(1, enable_type=1)
            == bytes.fromhex("3305f001ff011e00000000000000000000000027")
            and build_daysync_mode(1, enable_type=0)
            == bytes.fromhex("3305f001ff000000000000000000000000000038")
            and build_daysync_mode(2, scheme_id=3)
            == bytes.fromhex("3305f002030000000000000000000000000000c7")
            and build_daysync_mode(3, scheme_id=3)
            == bytes.fromhex("3305f003030000000000000000000000000000c6")
            and build_daysync_mode(4, scheme_id=3, repeat_frequency=0x81)
            == bytes.fromhex("3305f00403810000000000000000000000000040")
        ),
    ),
    _check(
        "build_daysync_mode",
        lambda: bool(
            build_daysync_mode(1, enable_type=1)[:8]
            == bytes([0x33, 0x05, 0xF0, 0x01, 0xFF, 0x01, 0x1E, 0x00])
            and build_daysync_mode(4, scheme_id=3, repeat_frequency=0x7F)[:6]
            == bytes([0x33, 0x05, 0xF0, 0x04, 0x03, 0x7F])
            and build_daysync_mode(0)[:4] == bytes([0x33, 0x05, 0xF0, 0x00])
            and build_daysync_mode(3, scheme_id=2)[:5] == bytes([0x33, 0x05, 0xF0, 0x03, 0x02])
        ),
    ),
    _check(
        "build_daysync_mode",
        lambda: bool(
            build_daysync_mode(1, enable_type=1)[:8]
            == bytes([0x33, 0x05, 0xF0, 0x01, 0xFF, 0x01, 0x1E, 0x00])
            and build_daysync_mode(4, scheme_id=3, repeat_frequency=0x7F)[:6]
            == bytes([0x33, 0x05, 0xF0, 0x04, 0x03, 0x7F])
            and build_daysync_mode(0)[:4] == bytes([0x33, 0x05, 0xF0, 0x00])
            and build_daysync_mode(2, scheme_id=5)[:5] == bytes([0x33, 0x05, 0xF0, 0x02, 0x05])
            and build_daysync_mode(1, enable_type=0)[:7] == bytes([0x33, 0x05, 0xF0, 0x01, 0xFF, 0x00, 0x00])
        ),
    ),
    _check(
        "build_daysync_refresh_city",
        lambda: bool(build_daysync_refresh_city() == bytes([0x33, 0xF0] + [0] * 17 + [0x33 ^ 0xF0])),
    ),
    _check(
        "build_daysync_refresh_city",
        lambda: bool(
            build_daysync_refresh_city() == bytes.fromhex("33f00000000000000000000000000000000000c3")
        ),
    ),
    _check(
        "build_daysync_refresh_city",
        lambda: bool(
            build_daysync_refresh_city()[:3] == bytes([0x33, 0xF0, 0x00])
            and build_daysync_refresh_city()[19] == 0x33 ^ 0xF0
        ),
    ),
    _check(
        "build_daysync_refresh_city",
        lambda: bool(build_daysync_refresh_city() == bytes([0x33, 0xF0] + [0] * 17 + [0x33 ^ 0xF0])),
    ),
    _check(
        "read_daysync_running_node",
        lambda: bool(read_daysync_running_node() == bytes([0xAA, 0xF0] + [0] * 17 + [0xAA ^ 0xF0])),
    ),
    _check(
        "read_daysync_running_node",
        lambda: bool(
            read_daysync_running_node() == bytes.fromhex("aaf000000000000000000000000000000000005a")
        ),
    ),
    _check(
        "read_daysync_running_node",
        lambda: bool(
            read_daysync_running_node()[:3] == bytes([0xAA, 0xF0, 0x00])
            and read_daysync_running_node()[19] == 0xAA ^ 0xF0
        ),
    ),
    _check(
        "read_daysync_running_node",
        lambda: bool(read_daysync_running_node() == bytes([0xAA, 0xF0] + [0] * 17 + [0xAA ^ 0xF0])),
    ),
    _check(
        "read_daysync_running",
        lambda: bool(read_daysync_running() == bytes([0xAA, 0xF1] + [0] * 17 + [0xAA ^ 0xF1])),
    ),
    _check(
        "read_daysync_running",
        lambda: bool(
            read_daysync_running()[:3] == bytes([0xAA, 0xF1, 0x00])
            and read_daysync_running()[19] == 0xAA ^ 0xF1
        ),
    ),
    _check(
        "read_daysync_running",
        lambda: bool(
            read_daysync_running()[:3] == bytes([0xAA, 0xF1, 0x00])
            and read_daysync_running()[19] == 0xAA ^ 0xF1
        ),
    ),
    _check(
        "read_daysync_running",
        lambda: bool(read_daysync_running() == bytes([0xAA, 0xF1] + [0] * 17 + [0xAA ^ 0xF1])),
    ),
    _check(
        "build_brightness",
        lambda: bool(
            build_brightness(50)[:4] == bytes([0x33, 0x04, 50, 0x00])
            and build_brightness(50)[19] == (0x33 ^ 0x04 ^ 50)
        ),
    ),
    _check(
        "build_check_ic",
        lambda: bool(
            build_check_ic(1, 300, 0) == bytes.fromhex("33460100012c0000000000000000000000000059")
            and build_check_ic(1, 300) == bytes.fromhex("334601012c000000000000000000000000000059")
            and build_check_ic(4, cmd=0x5A) == bytes.fromhex("335a04000000000000000000000000000000006d")
            and build_check_ic(5, 300, 0) == bytes.fromhex("33460500012c000000000000000000000000005d")
            and build_check_ic(4, 0x0123, 1)[:6] == bytes([0x33, 0x46, 0x04, 0x01, 0x01, 0x23])
            and build_check_ic(4, 0x0123)[:6] == bytes([0x33, 0x46, 0x04, 0x01, 0x23, 0x00])
            and build_check_ic(0) == append_bcc(bytes([0x33, 0x46, 0x00]))
        ),
    ),
    _check(
        "build_combination_effect_value",
        lambda: bool(
            build_combination_effect_value(10, [(range(10), bytes.fromhex("0001330102ffffffffffff"))])
            == bytes.fromhex("000102ff030a00000a0001330102ffffffffffff")
            and build_combination_effect_value(8, [([0, 2], bytes([0x50, 0xAA, 0xBB]))])
            == bytes([0x00, 0x01, 0x01, 0x05, 0x0A, 0x00, 0x00, 0x02, 0x00, 0xAA, 0xBB])
            and build_combination_effect_value(10, [([0], bytes([0x50, 0x01])), ([9], bytes([0x50, 0x01]))])
            == bytes([0x00, 0x01, 0x02, 0x01, 0x02, 0x0A, 0x00, 0x00, 0x01, 0x00, 0x01])
            and build_combination_effect_value(8, [([0, 1], bytes([0x00, 0xAA])), ([1], bytes([0x00, 0xBB]))])
            == bytes(
                [
                    0x00,
                    0x02,
                    0x01,
                    0x01,
                    0x0A,
                    0x00,
                    0x00,
                    0x01,
                    0x00,
                    0xAA,
                    0x02,
                    0x0A,
                    0x00,
                    0x00,
                    0x01,
                    0x00,
                    0xBB,
                ]
            )
            and build_combination_effect_value(8, [([], bytes([0x00, 0xAA])), ([8], bytes([0x00, 0xBB]))])
            == bytes([0x00, 0x00, 0x01])
        ),
    ),
    _check(
        "build_daysync_scheme_upload",
        lambda: bool(
            build_daysync_scheme_upload(
                bytes([3, 0x7F, 1, 1, 8, 0, 0, 0, 1, 1, 7, 0, 80, 1, 1, 1]),
                [bytes([8, 0, 0, 0, 1, 1, 7, 0, 80, 1, 1, 1])],
                100,
                4,
                bytes(range(8)),
            )
            == [
                [
                    bytes(
                        [
                            0xA6,
                            0,
                            0,
                            1,
                            1,
                            1,
                            0,
                            1,
                            2,
                            3,
                            4,
                            5,
                            6,
                            7,
                            2,
                            0,
                            0x63,
                            3,
                            0x7F,
                            1,
                            1,
                            8,
                            0,
                            0,
                            0,
                            1,
                            1,
                            7,
                            0,
                            80,
                            1,
                            1,
                            1,
                            0xE4,
                        ]
                    ),
                    bytes([0xA6, 0xFF, 0xFF, 0xA6]),
                ]
            ]
            and build_daysync_scheme_upload(bytes([3, 0x7F, 1, 0]), [], 100, 4, bytes(8)) == []
            and [
                len(f)
                for f in build_daysync_scheme_upload(
                    bytes([3, 0x7F, 1, 1, 8, 0, 0, 0, 1, 1, 7, 0, 80, 1, 1, 1]),
                    [bytes([8, 0, 0, 0, 1, 1, 7, 0, 80, 1, 1, 1])],
                    23,
                    1,
                    bytes(8),
                )[0]
            ]
            == [23, 15]
            and [
                [f[:6] + f[14:19] for f in pk[:1]] + [len(pk)]
                for pk in build_daysync_scheme_upload(
                    bytes([1, 0x7F, 1, 2]) + bytes([0x11] * 600) + bytes([0x22] * 600),
                    [bytes([0x11] * 600), bytes([0x22] * 600)],
                    100,
                    1,
                    [bytes([9] * 8), bytes([7] * 8)],
                )
            ]
            == [
                [bytes([0xA6, 0, 0, 1, 2, 1, 7, 0, 0x63, 1, 0x7F]), 7],
                [bytes([0xA6, 0, 0, 1, 2, 2, 7, 0, 0x63, 0x22, 0x22]), 7],
            ]
            and build_daysync_scheme_upload(
                bytes([1, 0x7F, 1, 2]) + bytes([0x11] * 600) + bytes([0x22] * 600),
                [bytes([0x11] * 600), bytes([0x22] * 600)],
                100,
                1,
                [bytes([9] * 8), bytes([7] * 8)],
            )[1][0][6:14]
            == bytes([7] * 8)
        ),
    ),
    _check(
        "build_logo",
        lambda: bool(
            build_logo(True, 50, 255, 0, 0)
            == bytes([0x33, 0xA6, 0x01, 50, 255, 0, 0] + [0] * 12 + [0x33 ^ 0xA6 ^ 0x01 ^ 50 ^ 255])
            and build_logo(False) == bytes([0x33, 0xA6] + [0] * 17 + [0x33 ^ 0xA6])
            and build_logo(False, 50, 255, 0, 0) == build_logo(False)
        ),
    ),
    _check(
        "build_low_blue_light",
        lambda: bool(
            build_low_blue_light(True, 30) == bytes.fromhex("33ba010164641e" + "00" * 12 + "97")
            and build_low_blue_light(False, 50)[:7] == bytes([0x33, 0xBA, 0x01, 0x00, 0x64, 0x64, 0x32])
            and build_low_blue_light(True, 0, scale=False, red=0, green=0)[:7]
            == bytes([0x33, 0xBA, 0x00, 0x01, 0x00, 0x00, 0x00])
        ),
    ),
    _check(
        "build_music_code_sens",
        lambda: bool(
            build_music_code_sens(0x40, 99) == bytes([0x33, 0x05, 0x13, 0x40, 0x63] + [0] * 14 + [0x06])
            and build_music_code_sens(65, 50, 2)[:7] == bytes([0x33, 0x05, 0x13, 0x41, 0x32, 0x02, 0x00])
            and build_music_code_sens(5, 100)[:6] == bytes([0x33, 0x05, 0x13, 0x05, 0x64, 0x00])
            and build_music_code_sens(0x30, 99)[:6] == bytes([0x33, 0x05, 0x13, 0x30, 0x63, 0x00])
            and build_music_code_sens(16, 50, sub_mode=0x11)[:6]
            == bytes([0x33, 0x05, 0x11, 0x10, 0x32, 0x00])
            and build_music_code_sens(0, 40, sub_mode=0x0C)[:6] == bytes([0x33, 0x05, 0x0C, 0x00, 0x28, 0x00])
            and build_music_code_sens(1, 90, 1, sub_mode=0x0C)[:7]
            == bytes([0x33, 0x05, 0x0C, 0x01, 0x5A, 0x01, 0x00])
            and build_music_code_sens(0, 50, 0, sub_mode=0x0C) == build_music_code_sens(0, 50, sub_mode=0x0C)
            and build_music_code_sens(50, 60, sub_mode=0x0F)[:6]
            == bytes([0x33, 0x05, 0x0F, 0x32, 0x3C, 0x00])
            and build_music_code_sens(0x40, 80, sub_mode=0x0E)[:6]
            == bytes([0x33, 0x05, 0x0E, 0x40, 0x50, 0x00])
            and build_music_code_sens(3, 99, sub_mode=0x01)[:6] == bytes([0x33, 0x05, 0x01, 0x03, 0x63, 0x00])
        ),
    ),
    _check(
        "build_relative_brightness",
        lambda: bool(
            build_relative_brightness(True, 10, 20, 30, 40, 50, 60)[:11]
            == bytes([0x33, 0xAE, 0x01, 0x06, 10, 20, 30, 40, 50, 60, 0x00])
            and build_relative_brightness(False, 1, 2, 3, 4)[:10]
            == bytes([0x33, 0xAE, 0x01, 0x04, 1, 2, 3, 4, 0, 0])
            and build_relative_brightness(False, 1, 2, 3, 4)[19]
            == bcc(build_relative_brightness(False, 1, 2, 3, 4))
            and build_relative_brightness(True, 10, 20, 30, 40, 50, 60)[19]
            == (0x33 ^ 0xAE ^ 0x01 ^ 0x06 ^ 10 ^ 20 ^ 30 ^ 40 ^ 50 ^ 60)
            and build_relative_brightness(True, 10, 20, 30, 40, 50, 60)[11:19] == bytes(8)
        ),
    ),
    _check(
        "build_scene",
        lambda: bool(
            build_scene(0x0203) == append_bcc(bytes([0x33, 0x05, 0x04, 0x03, 0x02]))
            and build_scene(0x0203)[:7] == bytes([0x33, 0x05, 0x04, 0x03, 0x02, 0x00, 0x00])
            and build_scene(0x0203, music_code=0x0506)[:7]
            == bytes([0x33, 0x05, 0x04, 0x03, 0x02, 0x06, 0x05])
            and build_scene(0x0203, music_code=0x0506)[19] == bcc(build_scene(0x0203, music_code=0x0506))
        ),
    ),
    _check(
        "build_scene_cube",
        lambda: bool(
            build_scene_cube(0x0102, 3, 45)[:8] == bytes([0x33, 0x05, 0x04, 0x02, 0x01, 0x03, 45, 0x00])
            and build_scene_cube(0x0102, 5, 45, bytes([0x10, 0x00, 0x20, 0x00]))[:12]
            == bytes([0x33, 0x05, 0x04, 0x02, 0x01, 0x05, 0x10, 0x00, 0x20, 0x00, 45, 0x00])
        ),
    ),
    _check(
        "build_scene_h61d3",
        lambda: bool(
            build_scene_h61d3(0x0102)[:8] == bytes([0x33, 0x05, 0x04, 0x02, 0x01, 0x00, 0x01, 0x00])
            and build_scene_h61d3(0x0102, 8, [3, 5])[:11]
            == bytes([0x33, 0x05, 0x04, 0x02, 0x01, 0x00, 0x07, 0x02, 0x03, 0x05, 0x00])
            and build_scene_h61d3(0x0102, 20)[5:7] == bytes([0xFF, 0xFF])
        ),
    ),
    _check(
        "build_scene_with_speed",
        lambda: bool(
            build_scene_with_speed(0x0102)[:7] == bytes([0x33, 0x05, 0x04, 0x02, 0x01, 100, 0x00])
            and build_scene_with_speed(0x0102, 50)[5] == 50
            and build_scene_with_speed(0x0102)[19] == bcc(build_scene_with_speed(0x0102))
        ),
    ),
    _check(
        "build_screen_bind_success",
        lambda: bool(
            build_screen_bind_success() == bytes.fromhex("33ba01" + "00" * 16 + "88")
            and build_screen_bind_success() == bytes([0x33, 0xBA, 0x01] + [0] * 16 + [0x33 ^ 0xBA ^ 0x01])
        ),
    ),
    _check(
        "build_status_request",
        lambda: bool(
            build_status_request([0x41, 0x30, 0x36, 0xA5])
            == append_bcc(bytes([0xAC, 0x03, 0x04, 0x41, 0x30, 0x36, 0xA5]))
            and build_status_request([0x41, 0x30, 0xA5])
            == append_bcc(bytes([0xAC, 0x03, 0x03, 0x41, 0x30, 0xA5]))
            and build_status_request([0x41, 0x30]) == append_bcc(bytes([0xAC, 0x03, 0x02, 0x41, 0x30]))
            and len(build_status_request([0x41, 0x30])) == 20
            and build_status_request([0x41, 0x30])[19] == bcc(build_status_request([0x41, 0x30]))
        ),
    ),
    _check(
        "build_switch",
        lambda: bool(
            build_switch(True) == bytes([0x33, 0x01, 0x01] + [0x00] * 16 + [0x33])
            and build_switch(False) == bytes([0x33, 0x01, 0x00] + [0x00] * 16 + [0x32])
        ),
    ),
    _check(
        "build_sync_time",
        lambda: bool(
            build_sync_time(13, 5, 9, 7, -5, 0)
            == bytes(
                [0x33, 0x09, 0x0D, 0x05, 0x09, 0x07, 0x01, 0xFB]
                + [0x00] * 11
                + [0x33 ^ 0x09 ^ 0x0D ^ 0x05 ^ 0x09 ^ 0x07 ^ 0x01 ^ 0xFB]
            )
        ),
    ),
    _check(
        "build_worry_free_at_night",
        lambda: bool(
            build_worry_free_at_night(True, 22, 0, 6, 30, 50, 20)[:10]
            == bytes([0x33, 0x36, 0x01, 22, 0, 6, 30, 50, 20, 0x00])
            and build_worry_free_at_night(True, 23, 0, 7, 0, 75, 20)
            == bytes.fromhex("333601170007004b14000000000000000000004b")
            and build_worry_free_at_night(False, 0, 0, 0, 0, 0, 0)
            == bytes.fromhex("3336000000000000000000000000000000000005")
        ),
    ),
    _check(
        "read_device_basic_info",
        lambda: bool(read_device_basic_info() == bytes([0xAA, 0x07, 0x10] + [0] * 16 + [0xBD])),
    ),
    _check(
        "read_device_info",
        lambda: bool(
            read_device_info(0x0A) == bytes([0xAA, 0x07, 0x0A] + [0] * 16 + [0xA7])
            and read_device_info(0x0B) == bytes([0xAA, 0x07, 0x0B] + [0] * 16 + [0xA6])
            and all(
                read_device_info(s) == bytes([0xAA, 0x07, s] + [0] * 16 + [0xAA ^ 0x07 ^ s])
                for s in (0x02, 0x03, 0x07, 0x10, 0x11)
            )
        ),
    ),
    _check(
        "read_device_wifi_info",
        lambda: bool(read_device_wifi_info() == bytes([0xAA, 0x07, 0x11] + [0] * 16 + [0xBC])),
    ),
    _check(
        "read_ic_segment",
        lambda: bool(read_ic_segment() == bytes.fromhex("aa400000000000000000000000000000000000ea")),
    ),
    _check(
        "read_switch",
        lambda: bool(
            read_switch() == bytes.fromhex("aa01" + "00" * 17 + "ab") and read_switch() == read_request(0x01)
        ),
    ),
]
