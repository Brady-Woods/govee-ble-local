# This is a generated file! Please edit source .ksy file and use kaitai-struct-compiler to rebuild
# type: ignore

import kaitaistruct
from kaitaistruct import KaitaiStruct, KaitaiStream, BytesIO
from enum import IntEnum


if getattr(kaitaistruct, 'API_VERSION', (0, 9)) < (0, 11):
    raise Exception("Incompatible Kaitai Struct Python API: 0.11 or later is required, but you have %s" % (kaitaistruct.__version__))

class GoveeBleFrame(KaitaiStruct):
    """One Govee BLE application frame. Every frame written to / notified on the vendor
    control characteristic (00010203-0405-0607-0809-0a0b0c0d2b11 write / ...2b10 notify)
    is 20 bytes for single-frame commands; the 0xA4 MTU MIDDLE/END packets and the small terminator are shorter:
    
        [ pro_type ][ body (size = frame_len - 2) ][ checksum ]
          byte 0      bytes 1 .. n-2                 byte n-1
    
    checksum = XOR of bytes 0..18. Kaitai expressions cannot fold over a byte range, so
    `checksum` is captured as a raw field and documented rather than recomputed here; a
    validator/builder must compute it separately (see GOVEE_BLE_GATT_PROTOCOL.md §4.1).
    
    This spec is parse-oriented (Kaitai generates readers). The write side is symmetric:
    build the same layout and append the XOR checksum. Multi-byte integers (e.g. kelvin)
    are big-endian, matching BleUtil.getSignedBytesFor2(value, hiFirst=true).
    
    Group control (com.govee.ble.group): the same 20-byte frames are written to multiple
    group members over dedicated group service/characteristic UUIDs, serialized with ~20-100 ms
    spacing (BleController.K). The frame layout is identical; only the transport fan-out differs.
    Advertisement (manufacturer-data) parsing is a separate bitstream — see govee_adv.ksy.
    """

    class Command(IntEnum):
        switch = 1
        brightness = 4
        mode = 5
        soft_version = 6
        device_info = 7
        sync_time = 9
        auto_time = 10
        delay_close = 11
        current_limit = 14
        light_num = 15
        sleep = 17
        wakeup = 18
        night_mode = 19
        gradual = 20
        light_indicator = 22
        wifi_link_start = 23
        wifi_hard_version = 32
        new_timer = 35
        read_ic_point = 36
        preset_color = 37
        take_photo_or_colortemp_type = 38
        preview_effect = 39
        delete_scene = 40
        sort_scene = 41
        light_direction_or_zone = 48
        camera_position = 49
        check_camera = 50
        volume = 51
        swap_light = 52
        without_interrupt = 53
        compose_light_switch = 54
        prompt_tone = 55
        check_direction = 57
        ic_num = 64
        on_off_memory = 65
        write_check_ic = 66
        check_ic_amount = 67
        calibration = 68
        check_ic = 70
        movie_feast_set_device = 80
        movie_feast_on_off = 84
        feast_carousel = 96
        combination_effect = 98
        daysync_op = 99
        music_lib_notify = 112
        music_lib_player_state = 113
        music_lib_preset_info = 114
        music_lib_preset_color = 115
        music_lib_preset_scenes = 116
        music_lib_set_play_state = 117
        music_lib_preview_effect = 118
        music_lib_remove_scene = 119
        music_lib_sort_scenes = 120
        music_lib_volume = 121
        music_lib_switch = 122
        set_alarm_clock = 132
        bulb_string_color_read = 162
        gradual_wifi_ble = 163
        local_color_read = 165
        white_balance = 169
        check_light = 170
        dynamic_api_support = 171
        plug_delay = 176
        secret_read = 177
        secret_write = 178
        plug_spec = 179
        plug_timer = 180
        plug_sync_time = 181
        notify_recognition = 237
        ota_prepare = 238

    class HandshakeOp(IntEnum):
        v1_request_session_key = 1
        v1_confirm = 2
        v2_single = 17
        v2_request_session_key = 25
        v2_confirm = 26

    class NotifySub(IntEnum):
        light_status = 1
        energy_saving = 2
        battery = 3
        music = 4
        pressure_or_volume = 5
        wifi_connect = 17
        brightness = 32
        take_photo_exit = 38
        device_info_or_zone = 48
        device_status = 64
        recognition = 237

    class Op15(IntEnum):
        set_mode = 0
        set_color = 1
        set_brightness = 2
        set_brightness_group = 3
        set_color_group = 4
        set_color_temp = 5

    class ProType(IntEnum):
        write = 51
        write_read = 58
        multi_write = 161
        multi_read = 162
        multi_write_v1 = 163
        multi_write_v2 = 164
        mic = 165
        read = 170
        bbq_status = 171
        multi_reply_read = 172
        handshake = 231
        notify = 238

    class SubMode(IntEnum):
        video = 0
        color_legacy = 2
        scene = 4
        mic = 5
        diy = 10
        color_rgbic_0b = 11
        music_alt = 12
        color_cct_0d = 13
        music_string = 14
        music_lamp = 15
        music_dreamcolor = 17
        music = 19
        color_rgbic_15 = 21
    def __init__(self, _io, _parent=None, _root=None):
        super(GoveeBleFrame, self).__init__(_io)
        self._parent = _parent
        self._root = _root or self
        self._read()

    def _read(self):
        self.pro_type = KaitaiStream.resolve_enum(GoveeBleFrame.ProType, self._io.read_u1())
        _on = self.pro_type
        if _on == GoveeBleFrame.ProType.handshake:
            pass
            self._raw_body = self._io.read_bytes(self._io.size() - 2)
            _io__raw_body = KaitaiStream(BytesIO(self._raw_body))
            self.body = GoveeBleFrame.Handshake(_io__raw_body, self, self._root)
        elif _on == GoveeBleFrame.ProType.multi_read:
            pass
            self._raw_body = self._io.read_bytes(self._io.size() - 2)
            _io__raw_body = KaitaiStream(BytesIO(self._raw_body))
            self.body = GoveeBleFrame.MultiA1(_io__raw_body, self, self._root)
        elif _on == GoveeBleFrame.ProType.multi_reply_read:
            pass
            self._raw_body = self._io.read_bytes(self._io.size() - 2)
            _io__raw_body = KaitaiStream(BytesIO(self._raw_body))
            self.body = GoveeBleFrame.MultiAc(_io__raw_body, self, self._root)
        elif _on == GoveeBleFrame.ProType.multi_write:
            pass
            self._raw_body = self._io.read_bytes(self._io.size() - 2)
            _io__raw_body = KaitaiStream(BytesIO(self._raw_body))
            self.body = GoveeBleFrame.MultiA1(_io__raw_body, self, self._root)
        elif _on == GoveeBleFrame.ProType.multi_write_v1:
            pass
            self._raw_body = self._io.read_bytes(self._io.size() - 2)
            _io__raw_body = KaitaiStream(BytesIO(self._raw_body))
            self.body = GoveeBleFrame.MultiA3(_io__raw_body, self, self._root)
        elif _on == GoveeBleFrame.ProType.multi_write_v2:
            pass
            self._raw_body = self._io.read_bytes(self._io.size() - 2)
            _io__raw_body = KaitaiStream(BytesIO(self._raw_body))
            self.body = GoveeBleFrame.MultiA4(_io__raw_body, self, self._root)
        elif _on == GoveeBleFrame.ProType.notify:
            pass
            self._raw_body = self._io.read_bytes(self._io.size() - 2)
            _io__raw_body = KaitaiStream(BytesIO(self._raw_body))
            self.body = GoveeBleFrame.NotifyFrame(_io__raw_body, self, self._root)
        elif _on == GoveeBleFrame.ProType.read:
            pass
            self._raw_body = self._io.read_bytes(self._io.size() - 2)
            _io__raw_body = KaitaiStream(BytesIO(self._raw_body))
            self.body = GoveeBleFrame.ReadCommand(_io__raw_body, self, self._root)
        elif _on == GoveeBleFrame.ProType.write:
            pass
            self._raw_body = self._io.read_bytes(self._io.size() - 2)
            _io__raw_body = KaitaiStream(BytesIO(self._raw_body))
            self.body = GoveeBleFrame.SingleCommand(_io__raw_body, self, self._root)
        else:
            pass
            self.body = self._io.read_bytes(self._io.size() - 2)
        self.checksum = self._io.read_u1()


    def _fetch_instances(self):
        pass
        _on = self.pro_type
        if _on == GoveeBleFrame.ProType.handshake:
            pass
            self.body._fetch_instances()
        elif _on == GoveeBleFrame.ProType.multi_read:
            pass
            self.body._fetch_instances()
        elif _on == GoveeBleFrame.ProType.multi_reply_read:
            pass
            self.body._fetch_instances()
        elif _on == GoveeBleFrame.ProType.multi_write:
            pass
            self.body._fetch_instances()
        elif _on == GoveeBleFrame.ProType.multi_write_v1:
            pass
            self.body._fetch_instances()
        elif _on == GoveeBleFrame.ProType.multi_write_v2:
            pass
            self.body._fetch_instances()
        elif _on == GoveeBleFrame.ProType.notify:
            pass
            self.body._fetch_instances()
        elif _on == GoveeBleFrame.ProType.read:
            pass
            self.body._fetch_instances()
        elif _on == GoveeBleFrame.ProType.write:
            pass
            self.body._fetch_instances()
        else:
            pass

    class A3Start(KaitaiStruct):
        def __init__(self, _io, _parent=None, _root=None):
            super(GoveeBleFrame.A3Start, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.marker = self._io.read_u1()
            self.packet_count = self._io.read_u1()
            self.comm_byte = self._io.read_u1()
            self.inline_value = self._io.read_bytes(14)


        def _fetch_instances(self):
            pass


    class A4StartHead(KaitaiStruct):
        def __init__(self, _io, _parent=None, _root=None):
            super(GoveeBleFrame.A4StartHead, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.marker = self._io.read_bytes(1)
            if not self.marker == b"\x01":
                raise kaitaistruct.ValidationNotEqualError(b"\x01", self.marker, self._io, u"/types/a4_start_head/seq/0")
            self.packet_count = self._io.read_u2le()
            self.comm_byte = self._io.read_u1()


        def _fetch_instances(self):
            pass


    class AreaMove(KaitaiStruct):
        """ParamsV2.AreaMoveEffect.c() :233: move_flags (canMove<<4 | order) + 3 movement params (f49663c/d/e)."""
        def __init__(self, _io, _parent=None, _root=None):
            super(GoveeBleFrame.AreaMove, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.move_flags = self._io.read_u1()
            self.p_c = self._io.read_u1()
            self.p_d = self._io.read_u1()
            self.p_e = self._io.read_u1()


        def _fetch_instances(self):
            pass


    class BarSwitchRead(KaitaiStruct):
        """AA 36 reply (h6047 ComposeLightController / ComposeLightHeartController, the app's heartbeat): [left, right, movie/feast switch] on/off (bArr[0]==1 / bArr[1]==1 / bArr[2]==1). The 33 36 write uses the same [left, right] (Controller4ExtBytes [54, left, right], NewDetailVm.D5). The app treats the device as ON when left OR right is on (NewDetailVm heart parse, Info4Detail.g). The third byte (the movie/feast mode switch, EventFeastHeart) is not needed for control. The left/right order can be swapped during setup (h6047/add/LightPosAc), so the names are logical, not physical."""
        def __init__(self, _io, _parent=None, _root=None):
            super(GoveeBleFrame.BarSwitchRead, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.left = self._io.read_u1()
            self.right = self._io.read_u1()
            if (not (self._io.is_eof())):
                pass
                self.movie_feast = self._io.read_u1()



        def _fetch_instances(self):
            pass
            if (not (self._io.is_eof())):
                pass



    class BarSwitchWrite(KaitaiStruct):
        """33 36 bar on/off write (H6047 two-bar lights): body = [left, right] (Controller4ExtBytes [0x36, left, right], NewDetailVm.D5; confirmed byte-exact vs the app). Same [left, right] the AA 36 reply echoes (bar_switch_read); the reply's 3rd byte (movie/feast switch) is read-only, not part of the write. left/right are logical positions (swappable at setup, h6047/add/LightPosAc), not fixed bars."""
        def __init__(self, _io, _parent=None, _root=None):
            super(GoveeBleFrame.BarSwitchWrite, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.left = self._io.read_u1()
            self.right = self._io.read_u1()


        def _fetch_instances(self):
            pass


    class BrightnessEffect(KaitaiStruct):
        """ParamsV2.BrightnessEffect (parser :269): 6 unsigned bytes f49670a..f49675f (level/index params; the app stores each as its own field but does not attach further semantics)."""
        def __init__(self, _io, _parent=None, _root=None):
            super(GoveeBleFrame.BrightnessEffect, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.f_a = self._io.read_u1()
            self.f_b = self._io.read_u1()
            self.f_c = self._io.read_u1()
            self.f_d = self._io.read_u1()
            self.f_e = self._io.read_u1()
            self.f_f = self._io.read_u1()


        def _fetch_instances(self):
            pass


    class BrightnessPayload(KaitaiStruct):
        def __init__(self, _io, _parent=None, _root=None):
            super(GoveeBleFrame.BrightnessPayload, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.level = self._io.read_u1()


        def _fetch_instances(self):
            pass


    class BrightnessReadReply(KaitaiStruct):
        """AA 04 reply (BrightnessController.parseValidBytes): raw brightness 0-255. NO 0-255<->0-100 rescale in the BLE codec (percent mapping is UI-layer)."""
        def __init__(self, _io, _parent=None, _root=None):
            super(GoveeBleFrame.BrightnessReadReply, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.brightness = self._io.read_u1()


        def _fetch_instances(self):
            pass


    class BulbGroupColorRead(KaitaiStruct):
        """Mechanism-B V1 batch frame (BulbGroupColor.parseBytes). Reply = [AA][A2][body]; this type = body
        (validBytes after the 2-byte header). 4 colour groups per frame, 3 bytes each [R,G,B], POSITIONAL
        (segment = (batch_seq-1)*4 + i; no per-group index/brightness byte — that is the V1↔V2 difference).
        Source: BulbGroupColor.java:15-29 (loop is hardcoded 4), BulbStringColorController.java:32-44 (cmd 0xA2),
        consumer BleOpV1.java:318-345 (batch accumulate).
        """
        def __init__(self, _io, _parent=None, _root=None):
            super(GoveeBleFrame.BulbGroupColorRead, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.batch_seq = self._io.read_u1()
            self.groups = []
            for i in range(4):
                self.groups.append(GoveeBleFrame.BulbRgbGroup(self._io, self, self._root))



        def _fetch_instances(self):
            pass
            for i in range(len(self.groups)):
                pass
                self.groups[i]._fetch_instances()



    class BulbGroupColorReadV2(KaitaiStruct):
        """Mechanism-B V2 batch frame (BulbGroupColorV2.parseBytes). Reply = [AA][A5][body]; this type = body.
        Each group = 4 bytes [brightness, R, G, B] — the leading byte is BRIGHTNESS, confirmed at the consumer:
        BulbGroupColorV2.f109039c -> SubModeColorV2.f109110e = colors.brightnessSet (SubModeColorV2.f:70).
        Segment is POSITIONAL (= (batch_seq-1)*groups_per_batch + i), NOT the leading byte. groups_per_batch is
        a CLIENT controller constant (BulbStringColorControllerV2.f109046g, default 3) — NOT frame-encoded;
        modelled at 3 (H61A8: 15 segments / 3 = 5 batches). Trailing bytes = zero padding. (A V3 variant,
        BulbStringColorControllerV3, also exists.) Source: BulbGroupColorV2.java:18-39,
        BulbStringColorControllerV2.java:41-53, consumer BleOpV1.java:349-377.
        """
        def __init__(self, _io, _parent=None, _root=None):
            super(GoveeBleFrame.BulbGroupColorReadV2, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.batch_seq = self._io.read_u1()
            self.groups = []
            for i in range(3):
                self.groups.append(GoveeBleFrame.BulbRgbGroupV2(self._io, self, self._root))



        def _fetch_instances(self):
            pass
            for i in range(len(self.groups)):
                pass
                self.groups[i]._fetch_instances()



    class BulbRgbGroup(KaitaiStruct):
        def __init__(self, _io, _parent=None, _root=None):
            super(GoveeBleFrame.BulbRgbGroup, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.r = self._io.read_u1()
            self.g = self._io.read_u1()
            self.b = self._io.read_u1()


        def _fetch_instances(self):
            pass


    class BulbRgbGroupV2(KaitaiStruct):
        def __init__(self, _io, _parent=None, _root=None):
            super(GoveeBleFrame.BulbRgbGroupV2, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.brightness = self._io.read_u1()
            self.r = self._io.read_u1()
            self.g = self._io.read_u1()
            self.b = self._io.read_u1()


        def _fetch_instances(self):
            pass


    class CctReadReply(KaitaiStruct):
        """Mode 0x15 CCT read reply. kelvin = u2be at frame bytes 4-5 (e.g. 0x0A8C=2700, 0x1964=6500); NO FF FF FF white point (unlike the write, which is [op, FF FF FF, kHi, kLo, ...]). Source: SubModeColorV2.parse / ComposeChange2ColorMode.result. H6047 (two bars) reads [gradual][kelvin left u2be][kelvin right u2be]: byte 0 is the gradual flag (== 1), the first kelvin is the LEFT bar (segments 0-4) and the second the RIGHT bar (segments 5-9); 0 = that bar is not in colour-temperature mode. The app shows one whole-device kelvin only when both bars match. Source: h6047/ble/SubModeColor.parse, h6047/detail/Info4Detail (subMode4Color IParseStrategy: q(bArr[0]==1), D0/E0(getSignedShort)), NewDetailVm.A5."""
        def __init__(self, _io, _parent=None, _root=None):
            super(GoveeBleFrame.CctReadReply, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.op = self._io.read_u1()
            self.kelvin = self._io.read_u2be()
            if (not (self._io.is_eof())):
                pass
                self.kelvin_right = self._io.read_u2be()

            self.trailing = self._io.read_bytes_full()


        def _fetch_instances(self):
            pass
            if (not (self._io.is_eof())):
                pass



    class Color0bKelvin(KaitaiStruct):
        """0x0b colour write body after the header 33 05 0b, ColorInstructionVersion COLOR_MODE_0X0B_KEVIN(6)
        (ColorInstructionVersion.java:19; Bytes.c
        base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Bytes.java:239-267 via Config.eu
        Config.java:13930-13934, Config.J[6] :5552-5556). NOT the color_rgbic_0b / color_rgbic_0b_wide shape:
        an 8-byte ext array [0x0b, R, G, B, kelvin hi, kelvin lo, m0, m1], no mask helper and no width choice.
        m0 bit k = position k (0-7, :251-258), m1 bit k = position 8+k (8-14 only, :259-265): position 15+ is
        never written, so bit 15 of seg_mask is always 0, and a posSet shorter than 15 entries throws
        ArrayIndexOutOfBounds in the app. kelvin = KelvinConfig.c(colour, info) (base-classes11/com/govee/
        base2light/pact/newdetail/config/fuc/KelvinConfig.java:119-122) = Constant.getColorTemKelvin(colour,
        min, max) (base-classes9/com/govee/base2home/Constant.java:591-594): the colour's Kelvin when it is a
        tint, else the low end of the device kelvinRange (not 0), big-endian (BleUtil.getSignedBytesFor2(k, true),
        Bytes.java:248-250). So kelvin != 0 does not mean CCT here. Like color_rgbic_0b_wide, the same 8-byte
        ext can instead travel as an 0xA3 type-0x6e multi-packet payload (ColorModeControllerConfig.j :689-712).
        """
        def __init__(self, _io, _parent=None, _root=None):
            super(GoveeBleFrame.Color0bKelvin, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.r = self._io.read_u1()
            self.g = self._io.read_u1()
            self.b = self._io.read_u1()
            self.kelvin = self._io.read_u2be()
            self.seg_mask = self._io.read_u2le()
            if not self.seg_mask <= 32767:
                raise kaitaistruct.ValidationGreaterThanError(32767, self.seg_mask, self._io, u"/types/color_0b_kelvin/seq/4")


        def _fetch_instances(self):
            pass


    class Color15(KaitaiStruct):
        def __init__(self, _io, _parent=None, _root=None):
            super(GoveeBleFrame.Color15, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.op_type = KaitaiStream.resolve_enum(GoveeBleFrame.Op15, self._io.read_u1())
            _on = self.op_type
            if _on == GoveeBleFrame.Op15.set_brightness:
                pass
                self._raw_data = self._io.read_bytes_full()
                _io__raw_data = KaitaiStream(BytesIO(self._raw_data))
                self.data = GoveeBleFrame.Op15Brightness(_io__raw_data, self, self._root)
            elif _on == GoveeBleFrame.Op15.set_color:
                pass
                self._raw_data = self._io.read_bytes_full()
                _io__raw_data = KaitaiStream(BytesIO(self._raw_data))
                self.data = GoveeBleFrame.Op15Color(_io__raw_data, self, self._root)
            elif _on == GoveeBleFrame.Op15.set_color_temp:
                pass
                self._raw_data = self._io.read_bytes_full()
                _io__raw_data = KaitaiStream(BytesIO(self._raw_data))
                self.data = GoveeBleFrame.Op15ColorTemp(_io__raw_data, self, self._root)
            else:
                pass
                self.data = self._io.read_bytes_full()


        def _fetch_instances(self):
            pass
            _on = self.op_type
            if _on == GoveeBleFrame.Op15.set_brightness:
                pass
                self.data._fetch_instances()
            elif _on == GoveeBleFrame.Op15.set_color:
                pass
                self.data._fetch_instances()
            elif _on == GoveeBleFrame.Op15.set_color_temp:
                pass
                self.data._fetch_instances()
            else:
                pass


    class ColorCct0d(KaitaiStruct):
        """0x0D colour write. RGB form = [R,G,B, 0...] (Bytes.f). Two CCT forms, both carrying tint =
        Constant.getTemColorByKelvin(kelvin) (exact 100 K table hit, else zeros):
          white-slot [ff,ff,ff, kelvin u2be, tint]  H6188 (Config.GM -> Bytes.A; library color_scheme "h6188");
          both-slot  [tint, kelvin u2be, tint]      H6008/H6006/H6052 (Config.bu -> Bytes.p; library "h6006").
        The app only sends whole 100 K steps, clamped to the device range (KelvinConfig.calibrationKelvin).
        """
        def __init__(self, _io, _parent=None, _root=None):
            super(GoveeBleFrame.ColorCct0d, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.r = self._io.read_u1()
            self.g = self._io.read_u1()
            self.b = self._io.read_u1()
            self.kelvin = self._io.read_u2be()
            self.tint_r = self._io.read_u1()
            self.tint_g = self._io.read_u1()
            self.tint_b = self._io.read_u1()


        def _fetch_instances(self):
            pass


    class ColorGroupRead(KaitaiStruct):
        """One 0xA5 group of the per-segment colour reply: group_index then record_count records. PARAMETRIC —
        the caller passes the two bits that are NOT in the byte stream, and the type switches internally:
          * record_count  = records in this group (= oneGroupColorSize, usually 4; last group = N - count*(g-1)).
          * has_brightness = 1 -> 4-byte [brightness,R,G,B] (color_group_record); 0 -> 3-byte [R,G,B]
            (color_group_record_rgb). Selected per SKU by supportPartBrightness. ALL CURATED devices
            (H60A6/H6047/H6641) are has_brightness=1 (4-byte). 3-byte is only non-curated colour-only families
            (tvlightv2, rgblight, bulblightstringv1, h7022, h6160). [record_size can't be auto-detected from the
            TLV len: len 13 == 4*3+1 == 3*4+1, so 3B and 4B are ambiguous — hence the explicit param.]
        """
        def __init__(self, record_count, has_brightness, _io, _parent=None, _root=None):
            super(GoveeBleFrame.ColorGroupRead, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self.record_count = record_count
            self.has_brightness = has_brightness
            self._read()

        def _read(self):
            self.group_index = self._io.read_u1()
            self.records = []
            for i in range(self.record_count):
                _on = self.has_brightness
                if _on == 0:
                    pass
                    self.records.append(GoveeBleFrame.ColorGroupRecordRgb(self._io, self, self._root))
                elif _on == 1:
                    pass
                    self.records.append(GoveeBleFrame.ColorGroupRecord(self._io, self, self._root))



        def _fetch_instances(self):
            pass
            for i in range(len(self.records)):
                pass
                _on = self.has_brightness
                if _on == 0:
                    pass
                    self.records[i]._fetch_instances()
                elif _on == 1:
                    pass
                    self.records[i]._fetch_instances()



    class ColorGroupRec(KaitaiStruct):
        def __init__(self, _io, _parent=None, _root=None):
            super(GoveeBleFrame.ColorGroupRec, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.brightness = self._io.read_u1()
            self.r = self._io.read_u1()
            self.g = self._io.read_u1()
            self.b = self._io.read_u1()


        def _fetch_instances(self):
            pass


    class ColorGroupRecRgb(KaitaiStruct):
        def __init__(self, _io, _parent=None, _root=None):
            super(GoveeBleFrame.ColorGroupRecRgb, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.r = self._io.read_u1()
            self.g = self._io.read_u1()
            self.b = self._io.read_u1()


        def _fetch_instances(self):
            pass


    class ColorGroupRecord(KaitaiStruct):
        def __init__(self, _io, _parent=None, _root=None):
            super(GoveeBleFrame.ColorGroupRecord, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.brightness = self._io.read_u1()
            self.r = self._io.read_u1()
            self.g = self._io.read_u1()
            self.b = self._io.read_u1()


        def _fetch_instances(self):
            pass


    class ColorGroupRecordRgb(KaitaiStruct):
        def __init__(self, _io, _parent=None, _root=None):
            super(GoveeBleFrame.ColorGroupRecordRgb, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.r = self._io.read_u1()
            self.g = self._io.read_u1()
            self.b = self._io.read_u1()


        def _fetch_instances(self):
            pass


    class ColorGroupStatus(KaitaiStruct):
        """0xAC TLV 0xA5 — ONE colour group (Controller4ColorInfoByGroup). group_index @ value[0] is 1-based
        (VM read loop iterates group 1..maxGroup; request byte = group number). Records fill the rest of the
        TLV: COUNT = (len-1)/record_size (the walker's own count is a caller-supplied per-SKU constant
        'oneGroupColorSize', but len == 1 + count*record_size, so len is authoritative and robust for a
        short last group). record_size is caller/SKU-selected: 4-byte [brightness,R,G,B] via generateReadController
        -> q():174 when the SKU supports part-brightness; 3-byte [R,G,B] via generateReadController4OnlyColor
        -> p():157 otherwise. ALL CURATED devices are 4-byte: H60A6 (VM4LightH60A6:302), H6047 (VM4H6038:872),
        H6641 (VM4LightH61d3:269, count=4). 3-byte is used by non-curated colour-only families (tvlightv2,
        rgblight, bulblightstringv1, h7022, h6160). Modelled 4-byte here (the curated form); for a 3-byte SKU
        each record is color_group_rec_rgb. Global segment index (client-assembled) = (group_index-1)*count + k.
        NOTE the reply's group_index byte is read but DISCARDED in the 4-byte callback path — the client tracks
        the group by its per-group READ REQUEST, not this byte.
        """
        def __init__(self, _io, _parent=None, _root=None):
            super(GoveeBleFrame.ColorGroupStatus, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.group_index = self._io.read_u1()
            self.records = []
            i = 0
            while not self._io.is_eof():
                self.records.append(GoveeBleFrame.ColorGroupRec(self._io, self, self._root))
                i += 1



        def _fetch_instances(self):
            pass
            for i in range(len(self.records)):
                pass
                self.records[i]._fetch_instances()



    class ColorLegacy(KaitaiStruct):
        def __init__(self, _io, _parent=None, _root=None):
            super(GoveeBleFrame.ColorLegacy, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.r = self._io.read_u1()
            self.g = self._io.read_u1()
            self.b = self._io.read_u1()
            self.white_flag = self._io.read_u1()
            self.r2 = self._io.read_u1()
            self.g2 = self._io.read_u1()
            self.b2 = self._io.read_u1()


        def _fetch_instances(self):
            pass


    class ColorRgbic0b(KaitaiStruct):
        def __init__(self, _io, _parent=None, _root=None):
            super(GoveeBleFrame.ColorRgbic0b, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.r = self._io.read_u1()
            self.g = self._io.read_u1()
            self.b = self._io.read_u1()
            self.seg_mask = self._io.read_u2le()


        def _fetch_instances(self):
            pass


    class ColorRgbic0bWide(KaitaiStruct):
        """0x0b RGBIC colour write body after the header 33 05 0b, ColorInstructionVersion COLOR_MODE_0X0B(3)
        (base-classes12/com/govee/common2light/capability/color/ColorInstructionVersion.java:16; Bytes.d
        base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Bytes.java:270-281 via Config.cu
        Config.java:13616-13620, Config.J[3] :5542-5546, J = map14 :5582, read by Config.u() :731-733 from
        ColorModeControllerConfig :265-279). The app builds a 17-byte ext array [0x0b (BleProtocol
        music_code_yuedong = 11), R, G, B, mask...] (ControllerMode cmd 0x05; AbsController.q truncates ext to
        17). Mask (Bytes.java:278): makeSelectedTwoBytes (always 2 bytes) when posSet has <= 16 entries, else
        BleUtils.X (ceil(n/8) bytes), copied at ext[4] with coerceAtMost(len, 13) (Bytes.java:279; 13 = 17 - 4),
        so positions 104+ are silently dropped. mask_width is therefore 2 for <= 16 pieces, else
        min(ceil(pieces/8), 13); never 0 or 1. With mask_width 2 this is byte-identical to color_rgbic_0b
        (u2le), which stays as is. NOT the colour-version-6 kelvin form (color_0b_kelvin below). Transport
        note: ColorModeControllerConfig.j :689-712 sends the same 17-byte ext ([0x0b, body, zero pad]) as the
        payload of an 0xA3 multi-packet of type 0x6e (MultiController.java:48-61) followed by 33 05 15, so this
        body can also follow the 0x0b byte inside that A3 payload rather than a 33 05 0b header.
        """
        def __init__(self, mask_width, _io, _parent=None, _root=None):
            super(GoveeBleFrame.ColorRgbic0bWide, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self.mask_width = mask_width
            self._read()

        def _read(self):
            self.r = self._io.read_u1()
            self.g = self._io.read_u1()
            self.b = self._io.read_u1()
            self.seg_mask = self._io.read_bytes(self.mask_width)


        def _fetch_instances(self):
            pass


    class ColorStripGroup(KaitaiStruct):
        """tag 0x00 = colour group [0, count, R, G, B, idx×count]; tag 0x01 = brightness group [1, count, brightness, idx×count]. idx = 1-byte segment indices."""
        def __init__(self, _io, _parent=None, _root=None):
            super(GoveeBleFrame.ColorStripGroup, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.tag = self._io.read_u1()
            self.pixel_count = self._io.read_u1()
            if self.tag == 0:
                pass
                self.r = self._io.read_u1()

            if self.tag == 0:
                pass
                self.g = self._io.read_u1()

            if self.tag == 0:
                pass
                self.b = self._io.read_u1()

            if self.tag == 1:
                pass
                self.brightness = self._io.read_u1()

            self.indices = []
            for i in range(self.pixel_count):
                self.indices.append(self._io.read_u1())



        def _fetch_instances(self):
            pass
            if self.tag == 0:
                pass

            if self.tag == 0:
                pass

            if self.tag == 0:
                pass

            if self.tag == 1:
                pass

            for i in range(len(self.indices)):
                pass



    class ColorStripWrite(KaitaiStruct):
        """[group_count][group × group_count]; group_count = (#distinct colours) + (#distinct brightness values, if a brightnessSet was set)."""
        def __init__(self, _io, _parent=None, _root=None):
            super(GoveeBleFrame.ColorStripWrite, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.group_count = self._io.read_u1()
            self.groups = []
            for i in range(self.group_count):
                self.groups.append(GoveeBleFrame.ColorStripGroup(self._io, self, self._root))



        def _fetch_instances(self):
            pass
            for i in range(len(self.groups)):
                pass
                self.groups[i]._fetch_instances()



    class DeviceInfoBasic(KaitaiStruct):
        def __init__(self, _io, _parent=None, _root=None):
            super(GoveeBleFrame.DeviceInfoBasic, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.uid = self._io.read_bytes(8)
            self.sw_version = GoveeBleFrame.Version3(self._io, self, self._root)
            self.hw_version = GoveeBleFrame.Version3(self._io, self, self._root)
            self.dsp_version = self._io.read_u2le()
            self.pad = self._io.read_bytes_full()


        def _fetch_instances(self):
            pass
            self.sw_version._fetch_instances()
            self.hw_version._fetch_instances()


    class DeviceInfoHardVersion(KaitaiStruct):
        def __init__(self, _io, _parent=None, _root=None):
            super(GoveeBleFrame.DeviceInfoHardVersion, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.version = (KaitaiStream.bytes_terminate(self._io.read_bytes_full(), 0, False)).decode(u"UTF-8")


        def _fetch_instances(self):
            pass


    class DeviceInfoRead(KaitaiStruct):
        """AA 07 reply (BasicInfoController / BasicWifiInfoController / SnController / HardVersionController).
        selector @ body[0] picks the body. 3-byte version fields render as X.YY.ZZ (major . %02d . %02d).
        The app's own connect-time reads are AA 06 (soft version), AA 07 03 (hard version) and AA 07 02
        (SN = device id); AA 07 10 / 11 are the binary basic / Wi-Fi forms.
        """
        def __init__(self, _io, _parent=None, _root=None):
            super(GoveeBleFrame.DeviceInfoRead, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.selector = self._io.read_u1()
            _on = self.selector
            if _on == 16:
                pass
                self._raw_info = self._io.read_bytes_full()
                _io__raw_info = KaitaiStream(BytesIO(self._raw_info))
                self.info = GoveeBleFrame.DeviceInfoBasic(_io__raw_info, self, self._root)
            elif _on == 17:
                pass
                self._raw_info = self._io.read_bytes_full()
                _io__raw_info = KaitaiStream(BytesIO(self._raw_info))
                self.info = GoveeBleFrame.DeviceInfoWifi(_io__raw_info, self, self._root)
            elif _on == 2:
                pass
                self._raw_info = self._io.read_bytes_full()
                _io__raw_info = KaitaiStream(BytesIO(self._raw_info))
                self.info = GoveeBleFrame.DeviceInfoSn(_io__raw_info, self, self._root)
            elif _on == 3:
                pass
                self._raw_info = self._io.read_bytes_full()
                _io__raw_info = KaitaiStream(BytesIO(self._raw_info))
                self.info = GoveeBleFrame.DeviceInfoHardVersion(_io__raw_info, self, self._root)
            else:
                pass
                self.info = self._io.read_bytes_full()


        def _fetch_instances(self):
            pass
            _on = self.selector
            if _on == 16:
                pass
                self.info._fetch_instances()
            elif _on == 17:
                pass
                self.info._fetch_instances()
            elif _on == 2:
                pass
                self.info._fetch_instances()
            elif _on == 3:
                pass
                self.info._fetch_instances()
            else:
                pass


    class DeviceInfoSn(KaitaiStruct):
        def __init__(self, _io, _parent=None, _root=None):
            super(GoveeBleFrame.DeviceInfoSn, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.uid = self._io.read_bytes(8)
            self.pad = self._io.read_bytes_full()


        def _fetch_instances(self):
            pass


    class DeviceInfoWifi(KaitaiStruct):
        def __init__(self, _io, _parent=None, _root=None):
            super(GoveeBleFrame.DeviceInfoWifi, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.wifi_mac = self._io.read_bytes(6)
            self.wifi_sw_version = GoveeBleFrame.Version3(self._io, self, self._root)
            self.wifi_hw_version = GoveeBleFrame.Version3(self._io, self, self._root)
            self.pad = self._io.read_bytes_full()


        def _fetch_instances(self):
            pass
            self.wifi_sw_version._fetch_instances()
            self.wifi_hw_version._fetch_instances()


    class DiyKuosan(KaitaiStruct):
        def __init__(self, _io, _parent=None, _root=None):
            super(GoveeBleFrame.DiyKuosan, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.i = self._io.read_u1()
            self.t = self._io.read_u1()
            self.s = self._io.read_u1()
            self.direction = self._io.read_u1()
            self.l0 = self._io.read_u1()
            self.l1 = self._io.read_u1()
            self.q = self._io.read_u1()
            self.r = self._io.read_u1()
            self.color_count = self._io.read_u1()
            self.colors = []
            for i in range(self.color_count):
                self.colors.append(GoveeBleFrame.ScRgb(self._io, self, self._root))



        def _fetch_instances(self):
            pass
            for i in range(len(self.colors)):
                pass
                self.colors[i]._fetch_instances()



    class DiyLayer(KaitaiStruct):
        def __init__(self, _io, _parent=None, _root=None):
            super(GoveeBleFrame.DiyLayer, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.layer_len = self._io.read_u2le()
            self._raw_effect = self._io.read_bytes(self.layer_len)
            _io__raw_effect = KaitaiStream(BytesIO(self._raw_effect))
            self.effect = GoveeBleFrame.DiySubEffect(_io__raw_effect, self, self._root)


        def _fetch_instances(self):
            pass
            self.effect._fetch_instances()


    class DiyLine(KaitaiStruct):
        """LiuDong4Line (id 2, flow/line): [i, direction, on] + count-prefixed byte list + [l0,l1,r,t,s] + RGB palette."""
        def __init__(self, _io, _parent=None, _root=None):
            super(GoveeBleFrame.DiyLine, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.speed = self._io.read_u1()
            self.direction = self._io.read_u1()
            self.on_flag = self._io.read_u1()
            self.seg_count = self._io.read_u1()
            self.seg = []
            for i in range(self.seg_count):
                self.seg.append(self._io.read_u1())

            self.l0 = self._io.read_u1()
            self.l1 = self._io.read_u1()
            self.r = self._io.read_u1()
            self.t = self._io.read_u1()
            self.s = self._io.read_u1()
            self.color_count = self._io.read_u1()
            self.colors = []
            for i in range(self.color_count):
                self.colors.append(GoveeBleFrame.ScRgb(self._io, self, self._root))



        def _fetch_instances(self):
            pass
            for i in range(len(self.seg)):
                pass

            for i in range(len(self.colors)):
                pass
                self.colors[i]._fetch_instances()



    class DiyLiudongArea(KaitaiStruct):
        def __init__(self, _io, _parent=None, _root=None):
            super(GoveeBleFrame.DiyLiudongArea, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.i = self._io.read_u1()
            self.t0 = self._io.read_u1()
            self.t1 = self._io.read_u1()
            self.x = self._io.read_u1()
            self.w = self._io.read_u1()
            self.h = self._io.read_u1()
            self.r0 = self._io.read_u1()
            self.r1 = self._io.read_u1()
            self.q0 = self._io.read_u1()
            self.q1 = self._io.read_u1()
            self.m0 = self._io.read_u1()
            self.m1 = self._io.read_u1()
            self.u = self._io.read_u1()
            self.v = self._io.read_u1()
            self.color_count = self._io.read_u1()
            self.colors = []
            for i in range(self.color_count):
                self.colors.append(GoveeBleFrame.ScRgb(self._io, self, self._root))



        def _fetch_instances(self):
            pass
            for i in range(len(self.colors)):
                pass
                self.colors[i]._fetch_instances()



    class DiyPalette2(KaitaiStruct):
        def __init__(self, _io, _parent=None, _root=None):
            super(GoveeBleFrame.DiyPalette2, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.i = self._io.read_u1()
            self.m = self._io.read_u1()
            self.color_count = self._io.read_u1()
            self.colors = []
            for i in range(self.color_count):
                self.colors.append(GoveeBleFrame.ScRgb(self._io, self, self._root))



        def _fetch_instances(self):
            pass
            for i in range(len(self.colors)):
                pass
                self.colors[i]._fetch_instances()



    class DiySubEffect(KaitaiStruct):
        """H60A6 DIY layer body = sub_effect_id + effect params (Layer.b, .../h60a6/diy/protocol/*). Map
        (pinyin = decompiled class name, English = translation): 1 LiuDong4Area (flow, zone) ·
        2 LiuDong4Line (flow, line) · 3 KuoSan (diffuse) · 4 XuanZhuan (rotate) · 5 SuiJiHuXi (random breathing) ·
        6 SuiJiLiuXing (random meteor) · 7 SuiJiJianBian (random gradient) · 8 Fade · 9 HuXi (breathing) ·
        10 ShanShuo (blink / twinkle). All except LiuDong4Line share the shape
        `[fixed params][color_count:u1][color_count × RGB]`; the arg to diy_palette is that fixed-param
        size. Verified: consumes every DIY layer body in the catalog exactly (ids 1,2,5,7 present).
        """
        def __init__(self, _io, _parent=None, _root=None):
            super(GoveeBleFrame.DiySubEffect, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.sub_effect_id = self._io.read_u1()
            _on = self.sub_effect_id
            if _on == 1:
                pass
                self.effect = GoveeBleFrame.DiyLiudongArea(self._io, self, self._root)
            elif _on == 10:
                pass
                self.effect = GoveeBleFrame.DiyPalette2(self._io, self, self._root)
            elif _on == 2:
                pass
                self.effect = GoveeBleFrame.DiyLine(self._io, self, self._root)
            elif _on == 3:
                pass
                self.effect = GoveeBleFrame.DiyKuosan(self._io, self, self._root)
            elif _on == 4:
                pass
                self.effect = GoveeBleFrame.DiyXuanzhuan(self._io, self, self._root)
            elif _on == 5:
                pass
                self.effect = GoveeBleFrame.DiySuijihuxi(self._io, self, self._root)
            elif _on == 6:
                pass
                self.effect = GoveeBleFrame.DiySuijiliuxing(self._io, self, self._root)
            elif _on == 7:
                pass
                self.effect = GoveeBleFrame.DiySuijijianbian(self._io, self, self._root)
            elif _on == 8:
                pass
                self.effect = GoveeBleFrame.DiyPalette2(self._io, self, self._root)
            elif _on == 9:
                pass
                self.effect = GoveeBleFrame.DiyPalette2(self._io, self, self._root)


        def _fetch_instances(self):
            pass
            _on = self.sub_effect_id
            if _on == 1:
                pass
                self.effect._fetch_instances()
            elif _on == 10:
                pass
                self.effect._fetch_instances()
            elif _on == 2:
                pass
                self.effect._fetch_instances()
            elif _on == 3:
                pass
                self.effect._fetch_instances()
            elif _on == 4:
                pass
                self.effect._fetch_instances()
            elif _on == 5:
                pass
                self.effect._fetch_instances()
            elif _on == 6:
                pass
                self.effect._fetch_instances()
            elif _on == 7:
                pass
                self.effect._fetch_instances()
            elif _on == 8:
                pass
                self.effect._fetch_instances()
            elif _on == 9:
                pass
                self.effect._fetch_instances()


    class DiySuijihuxi(KaitaiStruct):
        def __init__(self, _io, _parent=None, _root=None):
            super(GoveeBleFrame.DiySuijihuxi, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.i = self._io.read_u1()
            self.o = self._io.read_u1()
            self.n = self._io.read_u1()
            self.color_count = self._io.read_u1()
            self.colors = []
            for i in range(self.color_count):
                self.colors.append(GoveeBleFrame.ScRgb(self._io, self, self._root))



        def _fetch_instances(self):
            pass
            for i in range(len(self.colors)):
                pass
                self.colors[i]._fetch_instances()



    class DiySuijijianbian(KaitaiStruct):
        def __init__(self, _io, _parent=None, _root=None):
            super(GoveeBleFrame.DiySuijijianbian, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.i = self._io.read_u1()
            self.l0 = self._io.read_u1()
            self.l1 = self._io.read_u1()
            self.color_count = self._io.read_u1()
            self.colors = []
            for i in range(self.color_count):
                self.colors.append(GoveeBleFrame.ScRgb(self._io, self, self._root))



        def _fetch_instances(self):
            pass
            for i in range(len(self.colors)):
                pass
                self.colors[i]._fetch_instances()



    class DiySuijiliuxing(KaitaiStruct):
        def __init__(self, _io, _parent=None, _root=None):
            super(GoveeBleFrame.DiySuijiliuxing, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.i = self._io.read_u1()
            self.direction = self._io.read_u1()
            self.r = self._io.read_u1()
            self.s = self._io.read_u1()
            self.q = self._io.read_u1()
            self.p = self._io.read_u1()
            self.color_count = self._io.read_u1()
            self.colors = []
            for i in range(self.color_count):
                self.colors.append(GoveeBleFrame.ScRgb(self._io, self, self._root))



        def _fetch_instances(self):
            pass
            for i in range(len(self.colors)):
                pass
                self.colors[i]._fetch_instances()



    class DiyValue(KaitaiStruct):
        """H60A6 DIY effect: total-length prefix + background palette + per-layer sub-effect bodies."""
        def __init__(self, _io, _parent=None, _root=None):
            super(GoveeBleFrame.DiyValue, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.total_len = self._io.read_u2le()
            self.layer_size = self._io.read_u1()
            self.bg_color_size = self._io.read_u1()
            self.bg_colors = []
            for i in range(self.bg_color_size):
                self.bg_colors.append(GoveeBleFrame.ScRgb(self._io, self, self._root))

            self.bg_brightness = self._io.read_u1()
            self.layers = []
            for i in range(self.layer_size):
                self.layers.append(GoveeBleFrame.DiyLayer(self._io, self, self._root))



        def _fetch_instances(self):
            pass
            for i in range(len(self.bg_colors)):
                pass
                self.bg_colors[i]._fetch_instances()

            for i in range(len(self.layers)):
                pass
                self.layers[i]._fetch_instances()



    class DiyXuanzhuan(KaitaiStruct):
        def __init__(self, _io, _parent=None, _root=None):
            super(GoveeBleFrame.DiyXuanzhuan, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.i = self._io.read_u1()
            self.direction = self._io.read_u1()
            self.angle = self._io.read_u2le()
            self.p = self._io.read_u1()
            self.color_count = self._io.read_u1()
            self.colors = []
            for i in range(self.color_count):
                self.colors.append(GoveeBleFrame.ScRgb(self._io, self, self._root))



        def _fetch_instances(self):
            pass
            for i in range(len(self.colors)):
                pass
                self.colors[i]._fetch_instances()



    class GradualRead(KaitaiStruct):
        """AA A3 reply (ControllerGradual4BleWifi.h:100-101): gradual mode on iff byte0 == 1 (validBytes length 17; only byte0 is read)."""
        def __init__(self, _io, _parent=None, _root=None):
            super(GoveeBleFrame.GradualRead, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.state = self._io.read_u1()


        def _fetch_instances(self):
            pass


    class GradualWrite(KaitaiStruct):
        """Gradual (fade between colours) on/off write: body = [state]. Two command bytes, chosen by the device's GradualVersion (Config M table: 0 -> 0xA3, 1 -> 0x14, Config.java:5602-5613): 0xA3 = Bytes.w (base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Bytes.java:564-566) and 0x14 = Bytes.v (Bytes.java:559-560), each [cmd, on ? 1 : 0] wrapped by Controller4ExtBytes.d (split_rgbiclight/com/govee/rgbiclight/mode/color/ColorControllerConfig.java:507-510). The same [on ? 1 : 0] comes from ControllerGradual (cmd 0x14, base-classes11/com/govee/base2light/kt/general_controller/ControllerGradual.java:60,71-72), pact_h60a0 compose GradualController (cmd 0xA3, split_h60ax/com/govee/pact_h60a0/ble/controller/compose/GradualController.java:24,42-43), the legacy GradualController (cmd 0x14, split_rgbiclight/com/govee/dreamcolorlightv1/ble/GradualController.java:13-16,22-24,31-33), and the night-mode page (33 14 01 / 33 14 00, NightModeViewModel.java:86-114, where it means enter/leave night mode). The AA A3 reply is gradual_read."""
        def __init__(self, _io, _parent=None, _root=None):
            super(GoveeBleFrame.GradualWrite, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.state = self._io.read_u1()


        def _fetch_instances(self):
            pass


    class GraffitiColorGroup(KaitaiStruct):
        def __init__(self, _io, _parent=None, _root=None):
            super(GoveeBleFrame.GraffitiColorGroup, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.pixel_count = self._io.read_u2le()
            self.r = self._io.read_u1()
            self.g = self._io.read_u1()
            self.b = self._io.read_u1()
            self.pixel_indices = []
            for i in range(self.pixel_count):
                self.pixel_indices.append(self._io.read_u1())



        def _fetch_instances(self):
            pass
            for i in range(len(self.pixel_indices)):
                pass



    class GraffitiLayer(KaitaiStruct):
        def __init__(self, _io, _parent=None, _root=None):
            super(GoveeBleFrame.GraffitiLayer, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.rec_len = self._io.read_u2le()
            self._raw_body = self._io.read_bytes(self.rec_len)
            _io__raw_body = KaitaiStream(BytesIO(self._raw_body))
            self.body = GoveeBleFrame.GraffitiLayerBody(_io__raw_body, self, self._root)


        def _fetch_instances(self):
            pass
            self.body._fetch_instances()


    class GraffitiLayerBody(KaitaiStruct):
        def __init__(self, _io, _parent=None, _root=None):
            super(GoveeBleFrame.GraffitiLayerBody, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.graffiti_type = self._io.read_u1()
            self.inner_len = self._io.read_u2le()
            self.color_count = self._io.read_u2le()
            self.groups = []
            for i in range(self.color_count):
                self.groups.append(GoveeBleFrame.GraffitiColorGroup(self._io, self, self._root))

            self.action = self._io.read_u1()
            self.speed = self._io.read_u1()
            self.bg_brightness = self._io.read_u1()
            self.priority = self._io.read_u1()
            self.duration = self._io.read_u2le()
            self.reserved = self._io.read_bytes(4)
            if not self.reserved == b"\x00\x00\x00\x00":
                raise kaitaistruct.ValidationNotEqualError(b"\x00\x00\x00\x00", self.reserved, self._io, u"/types/graffiti_layer_body/seq/9")


        def _fetch_instances(self):
            pass
            for i in range(len(self.groups)):
                pass
                self.groups[i]._fetch_instances()



    class GraffitiV2Value(KaitaiStruct):
        """General RGBIC DIY-graffiti value (DiyGraffitiV2.g; shared by RgbIcGraffitiShare0x08 and
        DIYGraffitiParser). Distinct from the dialect-A library rgbic format (rgbic_scene_value).
        commByte is device-specific (H61A8 = 0x03). Header + flat colour-index group list.
        """
        def __init__(self, _io, _parent=None, _root=None):
            super(GoveeBleFrame.GraffitiV2Value, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.sub_effect = self._io.read_u1()
            self.speed = self._io.read_u1()
            self.brightness = self._io.read_u1()
            self.base_r = self._io.read_u1()
            self.base_g = self._io.read_u1()
            self.base_b = self._io.read_u1()
            self.group_count = self._io.read_u1()
            self.groups = []
            for i in range(self.group_count):
                self.groups.append(GoveeBleFrame.Gv2ColorGroup(self._io, self, self._root))



        def _fetch_instances(self):
            pass
            for i in range(len(self.groups)):
                pass
                self.groups[i]._fetch_instances()



    class GraffitiV3Value(KaitaiStruct):
        """H6052 graffiti-v3 effect: brightness + base colour + per-layer colour-index pixel map."""
        def __init__(self, _io, _parent=None, _root=None):
            super(GoveeBleFrame.GraffitiV3Value, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.brightness = self._io.read_u1()
            self.base_r = self._io.read_u1()
            self.base_g = self._io.read_u1()
            self.base_b = self._io.read_u1()
            self.layer_count = self._io.read_u1()
            self.layers = []
            for i in range(self.layer_count):
                self.layers.append(GoveeBleFrame.Gv3Layer(self._io, self, self._root))



        def _fetch_instances(self):
            pass
            for i in range(len(self.layers)):
                pass
                self.layers[i]._fetch_instances()



    class GraffitiValue(KaitaiStruct):
        """H60A6 graffiti effect: background colour + per-layer colour-index pixel map."""
        def __init__(self, _io, _parent=None, _root=None):
            super(GoveeBleFrame.GraffitiValue, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.marker = self._io.read_bytes(1)
            if not self.marker == b"\x20":
                raise kaitaistruct.ValidationNotEqualError(b"\x20", self.marker, self._io, u"/types/graffiti_value/seq/0")
            self.bg_r = self._io.read_u1()
            self.bg_g = self._io.read_u1()
            self.bg_b = self._io.read_u1()
            self.brightness = self._io.read_u1()
            self.show_type = self._io.read_u1()
            self.layer_count = self._io.read_u1()
            self.layers = []
            for i in range(self.layer_count):
                self.layers.append(GoveeBleFrame.GraffitiLayer(self._io, self, self._root))



        def _fetch_instances(self):
            pass
            for i in range(len(self.layers)):
                pass
                self.layers[i]._fetch_instances()



    class Gv2ColorGroup(KaitaiStruct):
        def __init__(self, _io, _parent=None, _root=None):
            super(GoveeBleFrame.Gv2ColorGroup, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.pixel_count = self._io.read_u1()
            self.r = self._io.read_u1()
            self.g = self._io.read_u1()
            self.b = self._io.read_u1()
            self.pixel_indices = []
            for i in range(self.pixel_count):
                self.pixel_indices.append(self._io.read_u1())



        def _fetch_instances(self):
            pass
            for i in range(len(self.pixel_indices)):
                pass



    class Gv3ColorGroup(KaitaiStruct):
        def __init__(self, _io, _parent=None, _root=None):
            super(GoveeBleFrame.Gv3ColorGroup, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.pixel_count = self._io.read_u1()
            self.r = self._io.read_u1()
            self.g = self._io.read_u1()
            self.b = self._io.read_u1()
            self.pixel_indices = []
            for i in range(self.pixel_count):
                self.pixel_indices.append(self._io.read_u1())



        def _fetch_instances(self):
            pass
            for i in range(len(self.pixel_indices)):
                pass



    class Gv3Layer(KaitaiStruct):
        def __init__(self, _io, _parent=None, _root=None):
            super(GoveeBleFrame.Gv3Layer, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.body_len = self._io.read_u2le()
            self._raw_body = self._io.read_bytes(self.body_len)
            _io__raw_body = KaitaiStream(BytesIO(self._raw_body))
            self.body = GoveeBleFrame.Gv3LayerBody(_io__raw_body, self, self._root)


        def _fetch_instances(self):
            pass
            self.body._fetch_instances()


    class Gv3LayerBody(KaitaiStruct):
        def __init__(self, _io, _parent=None, _root=None):
            super(GoveeBleFrame.Gv3LayerBody, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.speed = self._io.read_u1()
            self.action = self._io.read_u1()
            self.priority = self._io.read_u1()
            self.color_count = self._io.read_u1()
            self.groups = []
            for i in range(self.color_count):
                self.groups.append(GoveeBleFrame.Gv3ColorGroup(self._io, self, self._root))



        def _fetch_instances(self):
            pass
            for i in range(len(self.groups)):
                pass
                self.groups[i]._fetch_instances()



    class H60a6SceneValue(KaitaiStruct):
        """H60A6 type-5 value = decode(scenceParam)[1:]. Disambiguated by the DIY length gate
        (Pro4H60A6Diy.c runs first): if u16le@0 + 2 == total size ⇒ DIY (diy_value, proType 0xA3);
        else ⇒ graffiti (graffiti_value, proType 0xA4-MTU). commByte 0x58 in both cases.
        """
        def __init__(self, _io, _parent=None, _root=None):
            super(GoveeBleFrame.H60a6SceneValue, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            _on = self.is_diy
            if _on == False:
                pass
                self._raw_body = self._io.read_bytes_full()
                _io__raw_body = KaitaiStream(BytesIO(self._raw_body))
                self.body = GoveeBleFrame.GraffitiValue(_io__raw_body, self, self._root)
            elif _on == True:
                pass
                self._raw_body = self._io.read_bytes_full()
                _io__raw_body = KaitaiStream(BytesIO(self._raw_body))
                self.body = GoveeBleFrame.DiyValue(_io__raw_body, self, self._root)
            else:
                pass
                self.body = self._io.read_bytes_full()


        def _fetch_instances(self):
            pass
            _on = self.is_diy
            if _on == False:
                pass
                self.body._fetch_instances()
            elif _on == True:
                pass
                self.body._fetch_instances()
            else:
                pass
            _ = self.declared_len
            if hasattr(self, '_m_declared_len'):
                pass


        @property
        def declared_len(self):
            if hasattr(self, '_m_declared_len'):
                return self._m_declared_len

            _pos = self._io.pos()
            self._io.seek(0)
            self._m_declared_len = self._io.read_u2le()
            self._io.seek(_pos)
            return getattr(self, '_m_declared_len', None)

        @property
        def is_diy(self):
            if hasattr(self, '_m_is_diy'):
                return self._m_is_diy

            self._m_is_diy = self.declared_len + 2 == self._io.size()
            return getattr(self, '_m_is_diy', None)


    class Handshake(KaitaiStruct):
        def __init__(self, _io, _parent=None, _root=None):
            super(GoveeBleFrame.Handshake, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.op = KaitaiStream.resolve_enum(GoveeBleFrame.HandshakeOp, self._io.read_u1())
            self.data = self._io.read_bytes_full()


        def _fetch_instances(self):
            pass


    class IcSegmentRead(KaitaiStruct):
        """AA 40 reply (ControllerOnlyReadIcSegmentNum, base2light/kt/general_controller). The ONLY live-BLE
        capability read: reports the addressable IC (lamp-bead) count and the segment count, so a client can
        discover segmentation at runtime instead of relying on the per-SKU device table. FIXED layout (not
        parameterized). Source: ControllerOnlyReadIcSegmentNum.c():getSignedShort(it2[0],it2[1]) + it2[2].
        """
        def __init__(self, _io, _parent=None, _root=None):
            super(GoveeBleFrame.IcSegmentRead, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.ic_count = self._io.read_u2be()
            self.segment = self._io.read_u1()


        def _fetch_instances(self):
            pass

        @property
        def ic_count_signed(self):
            """The IC count as the app reads it: getSignedShort sign-extends, so 0x8000-0xFFFF reads as -32768..-1. ic_count stays the raw u2be for existing readers."""
            if hasattr(self, '_m_ic_count_signed'):
                return self._m_ic_count_signed

            self._m_ic_count_signed = (self.ic_count - 65536 if self.ic_count >= 32768 else self.ic_count)
            return getattr(self, '_m_ic_count_signed', None)


    class InAreaMove(KaitaiStruct):
        """ParamsV2.InAreaMoveEffect.c() :524: move_flags (canMove<<4 | order) + 2 movement params (f49695c/d)."""
        def __init__(self, _io, _parent=None, _root=None):
            super(GoveeBleFrame.InAreaMove, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.move_flags = self._io.read_u1()
            self.p_c = self._io.read_u1()
            self.p_d = self._io.read_u1()


        def _fetch_instances(self):
            pass


    class LightDirectionWrite(KaitaiStruct):
        """33 30 single-byte write: body = [value]. Command 0x30 is family-dependent. H6038 installation/calibration direction: CalibrationController.Companion.c(b) = Controller4ExtBytes.d([48, b]) (split_h6047/com/govee/h6047/newdetail/h6038/controller/CalibrationController.java:42-45), b = the index of the selected direction image (6 choices, H6038CalibrationAc.java:185-208,280-287; the send button is enabled only after a pick), sent from VM4H6038.N5 (VM4H6038.java:809-815, called at H6038CalibrationAc.java:316) or the add-device flow (H6038CalibrationAc.java:292-298). The compose DirectionController.b(b) builds the same [b] (base-classes10/com/govee/base2light/ble/controller/compose/DirectionController.java:34-36,60-61; DefWriteComposeController4Single.java:137-149 adds 33 44 02 00 in g), but nothing in this decomp calls it. On H6057 the same [value] is the MAIN-LIGHT SWITCH, not a direction: ControllerSingle.c(command_light_switch = 48, [on ? 1 : 0]) (split_hollowlamp/com/govee/h6057/origin/newDetail/H6057DetailVM.java:950-956; h6057/origin/ble/BleProtocol.java:7). On H71xx ReportChartController writes [1] (base-classes12/com/govee/base_h71xx/sku_base/ble/controller/ReportChartController.java:24-31)."""
        def __init__(self, _io, _parent=None, _root=None):
            super(GoveeBleFrame.LightDirectionWrite, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.value = self._io.read_u1()


        def _fetch_instances(self):
            pass


    class MicColorFrame(KaitaiStruct):
        def __init__(self, _io, _parent=None, _root=None):
            super(GoveeBleFrame.MicColorFrame, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.pro_type = self._io.read_bytes(1)
            if not self.pro_type == b"\xA5":
                raise kaitaistruct.ValidationNotEqualError(b"\xA5", self.pro_type, self._io, u"/types/mic_color_frame/seq/0")
            self.command = self._io.read_u1()
            self.data = self._io.read_bytes(self._io.size() - 3)
            self.checksum = self._io.read_u1()


        def _fetch_instances(self):
            pass


    class ModeColor0dReport(KaitaiStruct):
        """Mode 0x05 sub-mode 0x0D colour read-back. Frame = [proType][05][0D][body…]; this type = the body
        (bytes after the 0x0D sub-mode byte). Body interpretation is DEVICE-SPECIFIC — chosen by the
        IParseStrategy each device's *InfoDetail installs on its SubMode4Color:
          * H6052 (tablelampv1, CUSTOM strategy) => [R, G, B]: a SINGLE colour (ColorUtils.G(vb0,vb1,vb2))
            fanned across the device's zones (getColorSize: H6052=2). This resolves the old "0x0d is write-only"
            note — H6052 DOES read colour back, as these 3 bytes, and only these: its Kelvin bytes are never
            read. Source: H6052InfoDetail.parseModeValidBytes :323-346 (validBytes[0]==0x0D -> colour strategy
            on validBytes[1:]) + :141-147; Govee Home 7.7.02 the same (H6052InfoDetail.G and its constructor).
          * DEFAULT SubMode4Color strategy (most other families) => [gradual_flag, kelvin u16-be], NOT RGB
            (iParseStrategy: q(vb0==1); s(getSignedShort(vb1,vb2))). For those devices reinterpret r as the
            gradual flag and g,b as the big-endian kelvin.
          * the 0x0d bulbs H6006 / H6008 (bulblight ModeParseStrategyConfig.a) and the rgblight H6188
            (rgblight ModeParseStrategyConfig.a) => [R, G, B, kelvin u16-be, tintR, tintG, tintB]; a kelvin > 0
            (read signed) => colour-temperature mode. Use mode_color_0d_typed(kind=2) for the full body.
        Modelled as the [R, G, B] form, the first three bytes of kinds 1 and 2. Trailing frame bytes are zero
        padding (unparsed).
        """
        def __init__(self, _io, _parent=None, _root=None):
            super(GoveeBleFrame.ModeColor0dReport, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.r = self._io.read_u1()
            self.g = self._io.read_u1()
            self.b = self._io.read_u1()


        def _fetch_instances(self):
            pass


    class ModeColor0dTyped(KaitaiStruct):
        """0x0D colour mode-report body, parametric on the device's parse strategy (devices.yaml `mode_0d_kind`):
          kind 0 = default SubMode4Color strategy  -> [gradual_flag, kelvin u16-be]          (mode_0d_kind: cct)
          kind 1 = H6052 custom strategy           -> [R, G, B]                               (mode_0d_kind: rgb)
          kind 2 = rgblight (H6188) and 0x0d bulb  -> [R, G, B, kelvin u16-be, tintR, tintG, tintB]
                   (H6006 / H6008) strategy                                        (mode_0d_kind: rgb_kelvin_tint)
        Kind 2 is ModeParseStrategyConfig.a, the same rule in two splits: rgblight (installed by VM4Light.v5
        for H6188) and bulblight (the non-H6001 branch). kelvin = getSignedShort(vb[3], vb[4]); a non-zero
        kelvin makes the strategy show the tint (vb[5..7]) rather than R/G/B, and kelvin 0 the R/G/B. The body
        mirrors the color_cct_0d write.
        The bulbs' path (Govee Home 7.7.02): bulblightv3 / bulblightv1 pact Model -> bulblight AcNewDetail ->
        VM4Light ($connectBleSuc$1 -> m5 -> j5 installs ModeParseStrategyConfig.a) ->
        OpInfo4Detail.parseModeValidBytes, which hands sub-modes 0x15 / 0x0b / 0x02 / 0x0d to that strategy and
        takes colour-temperature mode only for a kelvin > 0; H6188 the same (VM4Light$connectBleSuc$1 -> A5 ->
        v5). So a kelvin of 0x8000 or more, negative there, shows the tint as an RGB colour, not CCT; is_cct is
        the strategy's tint test (!= 0), not that one.
        Device side, static firmware analysis (not hardware-verified): the H6006 and H6008 firmware build
        the 0x0D reply as [0D][R][G][B][kelvin hi][kelvin lo] with every later byte zero, so the tint fields
        always arrive as 0 (H6006: firmware-h6006/decompiled/H6006_ble_dispatcher.c case 0xd at line 618,
        bytes at lines 629-633; H6008: firmware-h6008-wifi/decompiled/H6008_ble_dispatcher.c case 0xd at line
        383, bytes at lines 388-392). The kelvin is pre-zeroed there, so 0 most likely means RGB mode.
        Kind 1 is H6052 (tablelampv1, goodsType 22): Model.u -> H6052NewDetailActivity -> H6052NewDetailVM ->
        H6052InfoDetail.G, whose constructor installs a strategy reading only vb[0..2]; the kelvin bytes are
        never read. (tablelampv1 ble.SubModeColor.parse, which does read a kelvin at vb[3..4], serves the IoT /
        group op dialogs, not the control screen.)
        """
        def __init__(self, kind, _io, _parent=None, _root=None):
            super(GoveeBleFrame.ModeColor0dTyped, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self.kind = kind
            self._read()

        def _read(self):
            if self.kind == 0:
                pass
                self.gradual_flag = self._io.read_u1()

            if self.kind == 0:
                pass
                self.kelvin = self._io.read_u2be()

            if  ((self.kind == 1) or (self.kind == 2)) :
                pass
                self.r = self._io.read_u1()

            if  ((self.kind == 1) or (self.kind == 2)) :
                pass
                self.g = self._io.read_u1()

            if  ((self.kind == 1) or (self.kind == 2)) :
                pass
                self.b = self._io.read_u1()

            if self.kind == 2:
                pass
                self.report_kelvin = self._io.read_u2be()

            if self.kind == 2:
                pass
                self.tint_r = self._io.read_u1()

            if self.kind == 2:
                pass
                self.tint_g = self._io.read_u1()

            if self.kind == 2:
                pass
                self.tint_b = self._io.read_u1()



        def _fetch_instances(self):
            pass
            if self.kind == 0:
                pass

            if self.kind == 0:
                pass

            if  ((self.kind == 1) or (self.kind == 2)) :
                pass

            if  ((self.kind == 1) or (self.kind == 2)) :
                pass

            if  ((self.kind == 1) or (self.kind == 2)) :
                pass

            if self.kind == 2:
                pass

            if self.kind == 2:
                pass

            if self.kind == 2:
                pass

            if self.kind == 2:
                pass


        @property
        def is_cct(self):
            """kind 2 only: the strategy shows the tint (report_kelvin != 0, ModeParseStrategyConfig.a); OpInfo4Detail.parseModeValidBytes takes CCT only for report_kelvin_signed > 0."""
            if hasattr(self, '_m_is_cct'):
                return self._m_is_cct

            self._m_is_cct =  ((self.kind == 2) and (self.report_kelvin != 0)) 
            return getattr(self, '_m_is_cct', None)

        @property
        def is_cct_positive(self):
            """kind 2 only: CCT by the signed > 0 rule of OpInfo4Detail.parseModeValidBytes and of the rgb_kelvin_0d decoders (cube CubeNewInfo4Detail.U; h60a1 Info4Detail.Q:416-427 for H60A1/H60A4/H60A6), which read only [R,G,B,kelvin] and never the tint bytes. A kelvin <= 0 is RGB from r/g/b."""
            if hasattr(self, '_m_is_cct_positive'):
                return self._m_is_cct_positive

            self._m_is_cct_positive =  ((self.kind == 2) and (self.report_kelvin_signed > 0)) 
            return getattr(self, '_m_is_cct_positive', None)

        @property
        def kelvin_signed(self):
            """kind 0 kelvin as the app reads it (getSignedShort sign-extends: 0x8000-0xFFFF reads as -32768..-1)."""
            if hasattr(self, '_m_kelvin_signed'):
                return self._m_kelvin_signed

            if self.kind == 0:
                pass
                self._m_kelvin_signed = (self.kelvin - 65536 if self.kelvin >= 32768 else self.kelvin)

            return getattr(self, '_m_kelvin_signed', None)

        @property
        def report_kelvin_signed(self):
            """kind 2 kelvin as the app reads it (getSignedShort sign-extends: 0x8000-0xFFFF reads as -32768..-1)."""
            if hasattr(self, '_m_report_kelvin_signed'):
                return self._m_report_kelvin_signed

            if self.kind == 2:
                pass
                self._m_report_kelvin_signed = (self.report_kelvin - 65536 if self.report_kelvin >= 32768 else self.report_kelvin)

            return getattr(self, '_m_report_kelvin_signed', None)


    class ModePayload(KaitaiStruct):
        def __init__(self, _io, _parent=None, _root=None):
            super(GoveeBleFrame.ModePayload, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.sub_type = KaitaiStream.resolve_enum(GoveeBleFrame.SubMode, self._io.read_u1())
            _on = self.sub_type
            if _on == GoveeBleFrame.SubMode.color_cct_0d:
                pass
                self._raw_params = self._io.read_bytes_full()
                _io__raw_params = KaitaiStream(BytesIO(self._raw_params))
                self.params = GoveeBleFrame.ColorCct0d(_io__raw_params, self, self._root)
            elif _on == GoveeBleFrame.SubMode.color_legacy:
                pass
                self._raw_params = self._io.read_bytes_full()
                _io__raw_params = KaitaiStream(BytesIO(self._raw_params))
                self.params = GoveeBleFrame.ColorLegacy(_io__raw_params, self, self._root)
            elif _on == GoveeBleFrame.SubMode.color_rgbic_0b:
                pass
                self._raw_params = self._io.read_bytes_full()
                _io__raw_params = KaitaiStream(BytesIO(self._raw_params))
                self.params = GoveeBleFrame.ColorRgbic0b(_io__raw_params, self, self._root)
            elif _on == GoveeBleFrame.SubMode.color_rgbic_15:
                pass
                self._raw_params = self._io.read_bytes_full()
                _io__raw_params = KaitaiStream(BytesIO(self._raw_params))
                self.params = GoveeBleFrame.Color15(_io__raw_params, self, self._root)
            elif _on == GoveeBleFrame.SubMode.scene:
                pass
                self._raw_params = self._io.read_bytes_full()
                _io__raw_params = KaitaiStream(BytesIO(self._raw_params))
                self.params = GoveeBleFrame.ScenePayload(_io__raw_params, self, self._root)
            else:
                pass
                self.params = self._io.read_bytes_full()


        def _fetch_instances(self):
            pass
            _on = self.sub_type
            if _on == GoveeBleFrame.SubMode.color_cct_0d:
                pass
                self.params._fetch_instances()
            elif _on == GoveeBleFrame.SubMode.color_legacy:
                pass
                self.params._fetch_instances()
            elif _on == GoveeBleFrame.SubMode.color_rgbic_0b:
                pass
                self.params._fetch_instances()
            elif _on == GoveeBleFrame.SubMode.color_rgbic_15:
                pass
                self.params._fetch_instances()
            elif _on == GoveeBleFrame.SubMode.scene:
                pass
                self.params._fetch_instances()
            else:
                pass


    class ModeRead(KaitaiStruct):
        """Mode (0x05) read. Byte 0 is EITHER a request selector (0x01) OR a reply sub-mode byte
        (0x15 color / 0x04 scene / 0x13 music / 0x0d bulb/lamp color). Disambiguate by value: 0x01 => request;
        a known sub-mode => reply.
        - 0x15 CCT reply => cct_read_reply: [op=01][kHi][kLo], kelvin big-endian @ frame bytes 4-5; DROPS
          the FF FF FF white point the 0x33 write carries. H6047 appends a second kelvin (right bar,
          bytes 6-7) and its byte 0 is the gradual flag: [gradual][kelvin left u2be][kelvin right u2be].
        - 0x0d => mode_color_0d_report: a DEVICE-SPECIFIC body, typed by mode_color_0d_typed(kind): kind 1
          H6052 [R,G,B]; kind 2 the 0x0d bulbs H6006/H6008 and the rgblight H6188 [R,G,B, kelvin, tint];
          kind 0 the default [gradual, kelvin]. (Only the LEGACY Mode/SubModeColorV1 stack reads 0x0d, and
          0x15, as op/gradual alone, bArr[0]==1.)
        - 0x13 music => music_read_reply (FAMILY-DEPENDENT; see that type).
        """
        def __init__(self, _io, _parent=None, _root=None):
            super(GoveeBleFrame.ModeRead, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.selector_or_sub_mode = self._io.read_u1()
            _on = self.selector_or_sub_mode
            if _on == 13:
                pass
                self._raw_rest = self._io.read_bytes_full()
                _io__raw_rest = KaitaiStream(BytesIO(self._raw_rest))
                self.rest = GoveeBleFrame.ModeColor0dReport(_io__raw_rest, self, self._root)
            elif _on == 19:
                pass
                self._raw_rest = self._io.read_bytes_full()
                _io__raw_rest = KaitaiStream(BytesIO(self._raw_rest))
                self.rest = GoveeBleFrame.MusicReadReply(_io__raw_rest, self, self._root)
            elif _on == 21:
                pass
                self._raw_rest = self._io.read_bytes_full()
                _io__raw_rest = KaitaiStream(BytesIO(self._raw_rest))
                self.rest = GoveeBleFrame.CctReadReply(_io__raw_rest, self, self._root)
            else:
                pass
                self.rest = self._io.read_bytes_full()


        def _fetch_instances(self):
            pass
            _on = self.selector_or_sub_mode
            if _on == 13:
                pass
                self.rest._fetch_instances()
            elif _on == 19:
                pass
                self.rest._fetch_instances()
            elif _on == 21:
                pass
                self.rest._fetch_instances()
            else:
                pass


    class ModeStatus(KaitaiStruct):
        """0xAC TLV 0x05 — device's active mode: [sub_mode, params]. Same [sub_mode, params] shape as the 0x05 mode frame body (SubMode dispatch, u():349 -> .g() lambda). sub_mode seen = the active mode: colour (0x15/0x0D), CCT, scene (0x04), music (0x13), diy (0x0a). params layout is per sub_mode / device (see mode_read / color_* types); left size-eos here."""
        def __init__(self, _io, _parent=None, _root=None):
            super(GoveeBleFrame.ModeStatus, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.sub_mode = KaitaiStream.resolve_enum(GoveeBleFrame.SubMode, self._io.read_u1())
            self.params = self._io.read_bytes_full()


        def _fetch_instances(self):
            pass


    class MultiA1(KaitaiStruct):
        """comType@1, position@2, 16 data bytes@3..18. position 0x00 = start (data[0] = packet count), 0xFF = end."""
        def __init__(self, _io, _parent=None, _root=None):
            super(GoveeBleFrame.MultiA1, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.com_type = self._io.read_u1()
            self.position = self._io.read_u1()
            self.data = self._io.read_bytes(16)


        def _fetch_instances(self):
            pass


    class MultiA3(KaitaiStruct):
        def __init__(self, _io, _parent=None, _root=None):
            super(GoveeBleFrame.MultiA3, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.seq_no = self._io.read_u1()
            _on = self.seq_no
            if _on == 0:
                pass
                self._raw_frame = self._io.read_bytes_full()
                _io__raw_frame = KaitaiStream(BytesIO(self._raw_frame))
                self.frame = GoveeBleFrame.A3Start(_io__raw_frame, self, self._root)
            else:
                pass
                self.frame = self._io.read_bytes_full()


        def _fetch_instances(self):
            pass
            _on = self.seq_no
            if _on == 0:
                pass
                self.frame._fetch_instances()
            else:
                pass


    class MultiA4(KaitaiStruct):
        """0xA4 MTU frame (MultipleControllerCommV1.makeSendBytesMtu :409). ONE BLE write; a scene value spans
        several frames. seq_marker (bytes 1-2, u16 LE) discriminates the form:
          0x0000 = START · 0xFFFF = END · else = MIDDLE (packet index, 1-based).
        START also carries marker 0x01 (byte3), packet_count (bytes 4-5 = TOTAL frame count incl. START+END;
        the small case len<=mtuSize-8 is always 2), commByte (byte6), then value from byte7 (up to mtuSize-8
        bytes). MIDDLE/END carry value from byte3 (mtuSize-4 bytes, END = remainder). The final byte of every
        frame is the BCC (parsed by the frame-level `checksum`). No separate terminator in the multi-packet
        case — the END (FF FF) frame is the last data packet. Reassemble value = START.value ++ MIDDLE.value
        (ascending seq) ++ END.value, then parse with the scene-upload VALUE types.
        Verified against makeSendBytesMtu: Aurora (187 B, MTU 20) => 12 frames = START(12) + 10*MIDDLE(16) + END(15).
        """
        def __init__(self, _io, _parent=None, _root=None):
            super(GoveeBleFrame.MultiA4, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.seq_marker = self._io.read_u2le()
            if self.seq_marker == 0:
                pass
                self.start = GoveeBleFrame.A4StartHead(self._io, self, self._root)

            self.value = self._io.read_bytes_full()


        def _fetch_instances(self):
            pass
            if self.seq_marker == 0:
                pass
                self.start._fetch_instances()



    class MultiAc(KaitaiStruct):
        """Request: [0xAC, command, N, cmd_1 .. cmd_N] (N = count of requested sub-commands),
        e.g. AC 03 02 41 30 (H60A6 single-zone) / AC 03 03 41 30 A5 (dual-zone).
        Reply: a burst of 0xAC frames, tag @ byte1 (AbsController4BleSingleSendMultiBack.g/.i/.h):
        first chunk [AC, 00, pkts u2le, lastLen, cmd, sub, 12 data bytes @ offset 7], where pkts =
        total packet count incl. the first and the last chunk and lastLen = valid bytes in the last
        chunk; middle chunks (tag = position) 17 data bytes @ offset 2; the last chunk (tag 0xFF, or
        its position pkts-1, which isSameController/onResult also accept) is data-bearing: lastLen bytes
        @ offset 2. pkts == 1: lastLen (<= 12) bytes @ offset 7, no more chunks. Reassembled buffer is a
        TLV stream (type,len,value). Modelled here as the request form.
        """
        def __init__(self, _io, _parent=None, _root=None):
            super(GoveeBleFrame.MultiAc, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.command = self._io.read_u1()
            self.count = self._io.read_u1()
            self.requested_types = []
            for i in range(self.count):
                self.requested_types.append(self._io.read_u1())

            self.pad = self._io.read_bytes_full()


        def _fetch_instances(self):
            pass
            for i in range(len(self.requested_types)):
                pass



    class MusicReadReply(KaitaiStruct):
        """Mode 0x13 music read reply. FAMILY-DEPENDENT layout: H60A6 (SubModeMusicV1.parse:112-131) = [music_code][value][auto_color_flag(==0)][spec_color_flag(==0)][R][G][B], truncating to 2 bytes for new-music codes; base2light SubModeNewMusic.parse reads only [music_code][value]. (Sub-mode 0x16 SubModeAbsMusic = [u16 count LE][value]; not 0x13.) Discriminator is a FLAG, not length (frames are zero-padded to 20 bytes, so length is unusable): SubModeMusicV1.parse (dreamcolorlightv1 :112-131) reads bArr[0..3] unconditionally, then reads RGB bArr[4..6] IFF bArr[3] != 0. So spec_color_flag gates the RGB triplet. For base2light devices bArr[2..3] are the zero padding => dynamic=0, spec_color_flag=0, no RGB (a correct superset — this is the H60A6 parser applied universally)."""
        def __init__(self, _io, _parent=None, _root=None):
            super(GoveeBleFrame.MusicReadReply, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.music_code = self._io.read_u1()
            self.value = self._io.read_u1()
            self.dynamic = self._io.read_u1()
            self.spec_color_flag = self._io.read_u1()
            if self.spec_color_flag != 0:
                pass
                self.color_r = self._io.read_u1()

            if self.spec_color_flag != 0:
                pass
                self.color_g = self._io.read_u1()

            if self.spec_color_flag != 0:
                pass
                self.color_b = self._io.read_u1()



        def _fetch_instances(self):
            pass
            if self.spec_color_flag != 0:
                pass

            if self.spec_color_flag != 0:
                pass

            if self.spec_color_flag != 0:
                pass



    class NotifyFrame(KaitaiStruct):
        def __init__(self, _io, _parent=None, _root=None):
            super(GoveeBleFrame.NotifyFrame, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.sub_type = KaitaiStream.resolve_enum(GoveeBleFrame.NotifySub, self._io.read_u1())
            _on = self.sub_type
            if _on == GoveeBleFrame.NotifySub.brightness:
                pass
                self._raw_data = self._io.read_bytes_full()
                _io__raw_data = KaitaiStream(BytesIO(self._raw_data))
                self.data = GoveeBleFrame.NotifyLevel(_io__raw_data, self, self._root)
            elif _on == GoveeBleFrame.NotifySub.device_info_or_zone:
                pass
                self._raw_data = self._io.read_bytes_full()
                _io__raw_data = KaitaiStream(BytesIO(self._raw_data))
                self.data = GoveeBleFrame.NotifySwitchZone(_io__raw_data, self, self._root)
            elif _on == GoveeBleFrame.NotifySub.wifi_connect:
                pass
                self._raw_data = self._io.read_bytes_full()
                _io__raw_data = KaitaiStream(BytesIO(self._raw_data))
                self.data = GoveeBleFrame.NotifyWifi(_io__raw_data, self, self._root)
            else:
                pass
                self.data = self._io.read_bytes_full()


        def _fetch_instances(self):
            pass
            _on = self.sub_type
            if _on == GoveeBleFrame.NotifySub.brightness:
                pass
                self.data._fetch_instances()
            elif _on == GoveeBleFrame.NotifySub.device_info_or_zone:
                pass
                self.data._fetch_instances()
            elif _on == GoveeBleFrame.NotifySub.wifi_connect:
                pass
                self.data._fetch_instances()
            else:
                pass


    class NotifyLevel(KaitaiStruct):
        """0xEE 20 brightness push (DefParser.p): level 0-255 @ data[0] (unsigned)."""
        def __init__(self, _io, _parent=None, _root=None):
            super(GoveeBleFrame.NotifyLevel, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.level = self._io.read_u1()


        def _fetch_instances(self):
            pass


    class NotifySwitchZone(KaitaiStruct):
        """0xEE 30 switch/zone push — FAMILY-DEPENDENT. H60A6 (VM4LightH60A6.U5): data[0]=detail (ignored),
        main switch = bit0 of data[1], zone0 = bit0 of data[2], zone1 = bit0 of data[3]. Generic (DefParser.M):
        a single flags byte @ data[1] carries up to 4 sub-switches in bits 1-4 (DefParser.O = one switch in bit 1).
        Interpret per device (flags_a = that generic flags byte). ParseUtils.getBit2Boolean numbers bits from 1, so
        "bit 1" is the LSB (0x01). As live power (devices.yaml client_profile.power_live: push_30): bit 0 of flags_a
        (frame[3]) on H60A1/H60A4/H60A6, H6039, H6078 and H601E/H601F; bit 0 of flags_c (frame[5]) on H60A0; per
        bar flags_b == 1 / flags_c == 1 on H6038/H6048.
        """
        def __init__(self, _io, _parent=None, _root=None):
            super(GoveeBleFrame.NotifySwitchZone, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.detail = self._io.read_u1()
            self.flags_a = self._io.read_u1()
            self.flags_b = self._io.read_u1()
            self.flags_c = self._io.read_u1()
            self.rest = self._io.read_bytes_full()


        def _fetch_instances(self):
            pass


    class NotifyWifi(KaitaiStruct):
        """0xEE 11 wifi-connect push (DefParser.Q): status @ data[0]; 0 = connected."""
        def __init__(self, _io, _parent=None, _root=None):
            super(GoveeBleFrame.NotifyWifi, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.status = self._io.read_u1()


        def _fetch_instances(self):
            pass


    class Op15Brightness(KaitaiStruct):
        def __init__(self, _io, _parent=None, _root=None):
            super(GoveeBleFrame.Op15Brightness, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.pct = self._io.read_u1()
            self.seg_mask = self._io.read_u2le()


        def _fetch_instances(self):
            pass


    class Op15BrightnessWide(KaitaiStruct):
        """Per-segment brightness write body after the header 33 05 15 02 (Bytes.G
        base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Bytes.java:172-181), reached via
        Config.JR (Config.java:10567-10570; Config.L[0] :5591-5595, L = map16 :5601, read by Config.D() :223-225)
        for RelativeBrightnessVersion RELATIVE_BRIGHTNESS_0X15(0)
        (base-classes12/com/govee/common2light/capability/color/RelativeBrightnessVersion.java:14) from
        ColorModeControllerConfig.t :1069-1097, and directly from ColorModeControllerConfig.z :1184-1195 (one
        frame per distinct brightness; posSet length = piece count, BleUtils.r :1077-1097). The app builds a
        17-byte ext array [0x15, 0x02, pct, mask...] (ControllerMode cmd 0x05; AbsController.q :47-60 truncates
        ext to 17; BleUtils.p :1030-1041 frames it). Mask (Bytes.java:178): makeSelectedTwoBytes (BleUtils.Y,
        always 2 bytes) when posSet has <= 16 entries, else BleUtils.X (ceil(n/8) bytes), copied at ext[3]
        with coerceAtMost(len, 14) (Bytes.java:179; 14 = 17 - 3), so positions 112+ are silently dropped.
        mask_width is therefore 2 for <= 16 pieces, else min(ceil(pieces/8), 14); never 0 or 1. pct is written
        raw ((byte) i, Bytes.java:177, no clamp). Same body as legacy split_h6047/com/govee/h6047/ble/
        SubModeColor.java:173-182 (Math.min(len, 14) at :181) and SubModeColorV1 opType 0
        split_h60ax/com/govee/pact_h60a0/ble/v1/SubModeColorV1.java:672-683 (no cap there; the legacy writers
        key the width on their segment count, not the posSet length). Standalone because color_15's switch
        reaches the fixed-u2le op15_brightness and cannot pass mask_width; with mask_width 2 this is
        byte-identical to op15_brightness, which stays as is.
        """
        def __init__(self, mask_width, _io, _parent=None, _root=None):
            super(GoveeBleFrame.Op15BrightnessWide, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self.mask_width = mask_width
            self._read()

        def _read(self):
            self.pct = self._io.read_u1()
            self.seg_mask = self._io.read_bytes(self.mask_width)


        def _fetch_instances(self):
            pass


    class Op15Color(KaitaiStruct):
        """SubModeColorV1.getWriteBytes op 0x01 — a client-built WRITE. Three forms share op byte 0x01 with
        NO in-frame discriminator; the form is chosen by device family + colour mode (the opType arg i2),
        and frames are zero-padded to 20 bytes (BleUtils.o :1006-1016), so length cannot recover it either:
          basic            (i2==1,  :686) = [r,g,b][seg_mask u2le]
          H60A1/H60A6 RGB  (i2==12, :736) = [r,g,b][00 00 00 00 00][seg_mask u2le]
          H60A1/H60A6 CCT  (i2==11, :725) = [r,g,b][kelvin u2be][tintR][tintG][tintB][seg_mask u2le]
        RGB vs CCT are byte-identical in length and op byte. This trailer is therefore EXTERNALLY-KEYED
        (like rgb_scene_value.rest / adv manufacturer_data.rest): the encoder knows the form from i2; a
        decoder of an isolated padded frame cannot. seg_mask (makeSelectedTwoBytes, <=16-seg legacy stack)
        is the last meaningful 2 bytes before zero padding.
        """
        def __init__(self, _io, _parent=None, _root=None):
            super(GoveeBleFrame.Op15Color, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.r = self._io.read_u1()
            self.g = self._io.read_u1()
            self.b = self._io.read_u1()
            self.trailer = self._io.read_bytes_full()


        def _fetch_instances(self):
            pass


    class Op15ColorGroupWrite(KaitaiStruct):
        """Group colour write body after "33 05 15 04". The app writes [0x15, 0x04, group, RGB x n] with
        n = min(4, colours given) and NOTHING after the colours (the ext array is sized 3 + 3n exactly):
          Bytes.m   base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Bytes.java:393-402
          SubModeColorV1 opType 7   split_h60ax/com/govee/pact_h60a0/ble/v1/SubModeColorV1.java:711-719
          colours: BleUtils.w0(colorSet, 4) base-classes10/com/govee/base2kt/utils/BleUtils.java:1271-1283
            (coerceAtMost(4, len) colours, 3 bytes each, R,G,B from ColorUtils.n).
          h6062 striplight SubModeColor.getWriteBytes split_h6062/com/govee/striplight/ble/SubModeColor.java:
            340-359 writes the same body (up to 4 colours, then the 17-byte array's zeros).
        Op 0x04 is NOT only this body: Bytes.D (Bytes.java:133-139) writes [0x15, 0x04, kelvin u2be] for
        colour-instruction version 9 (COLOR_MODE_0X15_0X04). The client picks this type from the write it is
        issuing, never from the op byte.
        A whole-device write is ceil(N/4) such frames, each colour set chunked by 4; the LAST chunk carries
        N % 4 colours (4 when N % 4 == 0), so color_count is 1..4 and is not in the stream (a black colour
        00 00 00 cannot be told from the zero padding): the client passes it.
        group = start + chunk index (0-based chunk index):
          new-detail path ColorModeControllerConfig.l (base-classes11/.../fuc/color/ColorModeControllerConfig.java:
            871-897, group = i + i8 at :895), start = LightColorCapabilityCenter.M0 = colorBrightnessGroupMode
            raw value (base-classes10/com/govee/base2light/capability/color/LightColorCapabilityCenter.java:
            866-873); ColorBrightnessGroupMode GROUP_INDEX_FROM_0(0) / GROUP_INDEX_FROM_1(1)
            (base-classes12/com/govee/common2light/capability/color/ColorBrightnessGroupMode.java:13-14).
            The hard-coded fallback (Config.N -> Config.A, base-classes11/.../fuc/color/Config.java:412-423,
            map built at :3118-3314) returns 1 for every one of its 39 goodsTypes, so groups are 1-based
            unless a cloud capability spec says GROUP_INDEX_FROM_0.
          H60A0/H60A1 SubModeColorV1.Companion.e/f (:176-197, :250-274): l(i7 + 1, ...) -> always 1-based
            (H60A0: 13 colours -> groups 1..4 with 4,4,4,1 colours).
          h6062 striplight SubModeColor.makeSubModeColor (SubModeColor.java:73-100, 124-150): f = i + 1 ->
            always 1-based.
        """
        def __init__(self, color_count, _io, _parent=None, _root=None):
            super(GoveeBleFrame.Op15ColorGroupWrite, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self.color_count = color_count
            self._read()

        def _read(self):
            self.group = self._io.read_u1()
            _ = self.group
            if not  ((self.color_count >= 1) and (self.color_count <= 4)) :
                raise kaitaistruct.ValidationExprError(self.group, self._io, u"/types/op15_color_group_write/seq/0")
            self.colors = []
            for i in range(self.color_count):
                self.colors.append(GoveeBleFrame.ScRgb(self._io, self, self._root))



        def _fetch_instances(self):
            pass
            for i in range(len(self.colors)):
                pass
                self.colors[i]._fetch_instances()



    class Op15ColorTemp(KaitaiStruct):
        def __init__(self, _io, _parent=None, _root=None):
            super(GoveeBleFrame.Op15ColorTemp, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.kelvin_le = self._io.read_u2le()


        def _fetch_instances(self):
            pass


    class Op15ColorTyped(KaitaiStruct):
        """op 0x01 colour write, parametric on the opType the client is issuing (SubModeColorV1.getWriteBytes):
          variant 1  = basic            -> [r,g,b, seg_mask]
          variant 12 = H60A1/H60A6 RGB  -> [r,g,b, 00 00 00 00 00, seg_mask]
          variant 11 = H60A1/H60A6 CCT  -> [r,g,b, kelvin(BE), tintR, tintG, tintB, seg_mask]
        (The frame-path `op15_color` is size-eos because a captured frame carries no opType; use THIS when you
        are building/decoding a known write. Devices.yaml `color_scheme` + the write op pick the variant.)
        """
        def __init__(self, variant, _io, _parent=None, _root=None):
            super(GoveeBleFrame.Op15ColorTyped, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self.variant = variant
            self._read()

        def _read(self):
            self.r = self._io.read_u1()
            self.g = self._io.read_u1()
            self.b = self._io.read_u1()
            if self.variant == 12:
                pass
                self.zeros = self._io.read_bytes(5)

            if self.variant == 11:
                pass
                self.kelvin = self._io.read_u2be()

            if self.variant == 11:
                pass
                self.tint_r = self._io.read_u1()

            if self.variant == 11:
                pass
                self.tint_g = self._io.read_u1()

            if self.variant == 11:
                pass
                self.tint_b = self._io.read_u1()

            self.seg_mask = self._io.read_u2le()


        def _fetch_instances(self):
            pass
            if self.variant == 12:
                pass

            if self.variant == 11:
                pass

            if self.variant == 11:
                pass

            if self.variant == 11:
                pass

            if self.variant == 11:
                pass



    class Op15ColorWrite(KaitaiStruct):
        """Colour write body after "33 05 15 01", parametric on the form (variant) and the segment-mask width.
        The forms share op byte 0x01 with no in-frame discriminator (chosen by device family + colour mode):
          variant 1  basic            [r,g,b][seg_mask]
                     SubModeColorV1 opType 1, split_h60ax/com/govee/pact_h60a0/ble/v1/SubModeColorV1.java:684-694
          variant 12 H60A1/H60A6 RGB  [r,g,b][00 x5][seg_mask]
                     Bytes.h base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Bytes.java:315-326;
                     SubModeColorV1 opType 12 :734-744
          variant 11 CCT              [slot r,g,b][kelvin u2be][tint r,g,b][seg_mask]
                     Bytes.r Bytes.java:474-501, slot by kind i5 (:480-486): kind 0 -> 00 00 00 (ColorUtils.F());
                     kind 3 -> 00 00 00 when every position is set, else slot = tint with kelvin 0 and tint
                     00 00 00 (:492, :495; an RGB write); any other kind (1, the usual one) -> ff ff ff (R()).
                     SubModeColorV1 opType 11 :720-733 (slot = ColorUtils.R() = ff ff ff, tint = tempRgb).
                     The family writers use the same layout with the user colour in the slot and kelvin / tint
                     left 0 for RGB (byte-identical to variant 12 then): h6047 SubModeColor.getWriteBytes
                     split_h6047/com/govee/h6047/ble/SubModeColor.java:184-200; h6062 striplight
                     SubModeColor.getWriteBytes split_h6062/com/govee/striplight/ble/SubModeColor.java:376-405,
                     SubModeColorV2.j :570-587, SubModeColorV3.OpColor.makeBytes/e :63-85, 136-141.
          variant 5  colour-instruction version 5 (COLOR_MODE_0X15_NO_TEMP_RGB, Config.J[5] = Config.Zt,
                     Config.java:5527-5531, 13124-13131), e.g. H6061 (goodsType 51):
                     RGB    Bytes.l Bytes.java:379-390 -> [r,g,b][00 00][seg_mask]
                     Kelvin Bytes.u Bytes.java:541-556 -> [tint r,g,b][kelvin u2be][seg_mask]
                       (kelvin = Constant.getColorTemKelvin(tint)); RGB is the kelvin == 0 case.
        seg_mask: LSB-first, position 8j+k at bit k of byte j (BleUtils.X, base-classes10/com/govee/base2kt/
        utils/BleUtils.java:406-438). Width: 2 bytes (makeSelectedTwoBytes, BleUtils.java:444-494 /
        base-classes10/com/govee/base2light/ble/BleUtil.java:412-443; 00 00 if the set is empty) when the set
        has <= 16 positions, else ceil(n/8) (BleUtils.X). Bytes.* key it on posSet.length > 16; SubModeColorV1
        on its segment count > 16 (:672); h6047 on its segment count > 16 (:199). The h6062 writers size it
        otherwise (SubModeColorV2.j: BleUtil.makeBytes4SelectPosByOneBit ceil(n/8), BleUtil.java:383-398, with
        no 2-byte floor, fed 24 positions by NumUtil.makeSelectByByteNum(list, 3) at :151); a 1-byte mask is
        sent as mask_width 2 with a 00 high byte, which gives the same zero-padded frame.
        Cap = 17 - (ext bytes before the mask, sub-mode and op included):
          variant 1  mask at ext[5]  -> 12 bytes (96 positions). No coerceAtMost: the ext goes uncut to
                     BleUtils.p via the h60ax ModeController (compose/AbsController.java:24-26, not the kt
                     AbsController.q); a 13th byte lands on frame[19] and is overwritten by the BCC, 14+ throws.
          variant 12 mask at ext[10] -> 7 bytes (56 positions): Bytes.h coerceAtMost(len, 17 - 10) :324 drops
                     the rest silently; SubModeColorV1 opType 12 has no coerce (8th byte -> BCC, 9+ throws).
          variant 11 mask at ext[10] -> 7 bytes: Bytes.r coerceAtMost(len, 17 - 10) :499; h6047 min(len, 7)
                     :200; h6062 SubModeColor indexes a 17-byte array (ArrayIndexOutOfBounds past 56 positions).
          variant 5  mask at ext[7]  -> 10 bytes (80 positions): Bytes.l :388 / Bytes.u :554 copy uncoerced
                     into a 17-byte array, so 11+ bytes throws ArrayIndexOutOfBounds (no frame).
        The frame-path op15_color (size-eos) and op15_color_typed (2-byte mask; variants 1/12/11) stay as is.
        """
        def __init__(self, variant, mask_width, _io, _parent=None, _root=None):
            super(GoveeBleFrame.Op15ColorWrite, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self.variant = variant
            self.mask_width = mask_width
            self._read()

        def _read(self):
            self.r = self._io.read_u1()
            self.g = self._io.read_u1()
            self.b = self._io.read_u1()
            if self.variant == 12:
                pass
                self.reserved = self._io.read_bytes(5)
                if not self.reserved == b"\x00\x00\x00\x00\x00":
                    raise kaitaistruct.ValidationNotEqualError(b"\x00\x00\x00\x00\x00", self.reserved, self._io, u"/types/op15_color_write/seq/3")

            if  ((self.variant == 11) or (self.variant == 5)) :
                pass
                self.kelvin = self._io.read_u2be()

            if self.variant == 11:
                pass
                self.tint_r = self._io.read_u1()

            if self.variant == 11:
                pass
                self.tint_g = self._io.read_u1()

            if self.variant == 11:
                pass
                self.tint_b = self._io.read_u1()

            self.seg_mask = self._io.read_bytes(self.mask_width)
            _ = self.seg_mask
            if not  ((len(_) >= 2) and (len(_) <= self.mask_cap) and ( ((self.variant == 1) or (self.variant == 5) or (self.variant == 11) or (self.variant == 12)) )) :
                raise kaitaistruct.ValidationExprError(self.seg_mask, self._io, u"/types/op15_color_write/seq/8")


        def _fetch_instances(self):
            pass
            if self.variant == 12:
                pass

            if  ((self.variant == 11) or (self.variant == 5)) :
                pass

            if self.variant == 11:
                pass

            if self.variant == 11:
                pass

            if self.variant == 11:
                pass


        @property
        def is_cct(self):
            """kelvin != 0 (the ksy's is_cct convention): Bytes.u vs Bytes.l's 00 00, and Bytes.r kinds 0/1/3-full vs the kind-3 partial and h6047/h6062 RGB writes, which carry kelvin 0."""
            if hasattr(self, '_m_is_cct'):
                return self._m_is_cct

            self._m_is_cct =  (( ((self.variant == 11) or (self.variant == 5)) ) and (self.kelvin != 0)) 
            return getattr(self, '_m_is_cct', None)

        @property
        def mask_cap(self):
            """most mask bytes the app puts on the wire for this variant: 17 - ext bytes before the mask (5 / 7 / 10)."""
            if hasattr(self, '_m_mask_cap'):
                return self._m_mask_cap

            self._m_mask_cap = (12 if self.variant == 1 else (10 if self.variant == 5 else 7))
            return getattr(self, '_m_mask_cap', None)


    class PlugSpecRead(KaitaiStruct):
        """AA B3 reply (h5080 SpecController:19): single spec byte @ body[0]. TRACED all uses: stored as model.L (EventSpec.h, PairAcV1.onEventSpec:347) and forwarded as the IoT spec identifier / adjust-screen string (Model.getSpec) — it is never bit-decoded. Plug OUTLET COUNT is NOT this byte: it is Support.getPlugNum(goodsType) = 50/307->2, 90->3, else 1."""
        def __init__(self, _io, _parent=None, _root=None):
            super(GoveeBleFrame.PlugSpecRead, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.spec = self._io.read_u1()
            self.rest = self._io.read_bytes_full()


        def _fetch_instances(self):
            pass


    class PlugSyncTimePayload(KaitaiStruct):
        """Plug family, command 0xB5."""
        def __init__(self, _io, _parent=None, _root=None):
            super(GoveeBleFrame.PlugSyncTimePayload, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.unix_seconds = self._io.read_u4be()
            self.marker = self._io.read_u1()
            self.tz_hour = self._io.read_s1()
            self.tz_min = self._io.read_s1()


        def _fetch_instances(self):
            pass


    class ReadCommand(KaitaiStruct):
        def __init__(self, _io, _parent=None, _root=None):
            super(GoveeBleFrame.ReadCommand, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.command = KaitaiStream.resolve_enum(GoveeBleFrame.Command, self._io.read_u1())
            _on = self.command
            if _on == GoveeBleFrame.Command.brightness:
                pass
                self._raw_body = self._io.read_bytes_full()
                _io__raw_body = KaitaiStream(BytesIO(self._raw_body))
                self.body = GoveeBleFrame.BrightnessReadReply(_io__raw_body, self, self._root)
            elif _on == GoveeBleFrame.Command.bulb_string_color_read:
                pass
                self._raw_body = self._io.read_bytes_full()
                _io__raw_body = KaitaiStream(BytesIO(self._raw_body))
                self.body = GoveeBleFrame.BulbGroupColorRead(_io__raw_body, self, self._root)
            elif _on == GoveeBleFrame.Command.compose_light_switch:
                pass
                self._raw_body = self._io.read_bytes_full()
                _io__raw_body = KaitaiStream(BytesIO(self._raw_body))
                self.body = GoveeBleFrame.BarSwitchRead(_io__raw_body, self, self._root)
            elif _on == GoveeBleFrame.Command.device_info:
                pass
                self._raw_body = self._io.read_bytes_full()
                _io__raw_body = KaitaiStream(BytesIO(self._raw_body))
                self.body = GoveeBleFrame.DeviceInfoRead(_io__raw_body, self, self._root)
            elif _on == GoveeBleFrame.Command.gradual_wifi_ble:
                pass
                self._raw_body = self._io.read_bytes_full()
                _io__raw_body = KaitaiStream(BytesIO(self._raw_body))
                self.body = GoveeBleFrame.GradualRead(_io__raw_body, self, self._root)
            elif _on == GoveeBleFrame.Command.ic_num:
                pass
                self._raw_body = self._io.read_bytes_full()
                _io__raw_body = KaitaiStream(BytesIO(self._raw_body))
                self.body = GoveeBleFrame.IcSegmentRead(_io__raw_body, self, self._root)
            elif _on == GoveeBleFrame.Command.local_color_read:
                pass
                self._raw_body = self._io.read_bytes_full()
                _io__raw_body = KaitaiStream(BytesIO(self._raw_body))
                self.body = GoveeBleFrame.BulbGroupColorReadV2(_io__raw_body, self, self._root)
            elif _on == GoveeBleFrame.Command.mode:
                pass
                self._raw_body = self._io.read_bytes_full()
                _io__raw_body = KaitaiStream(BytesIO(self._raw_body))
                self.body = GoveeBleFrame.ModeRead(_io__raw_body, self, self._root)
            elif _on == GoveeBleFrame.Command.plug_spec:
                pass
                self._raw_body = self._io.read_bytes_full()
                _io__raw_body = KaitaiStream(BytesIO(self._raw_body))
                self.body = GoveeBleFrame.PlugSpecRead(_io__raw_body, self, self._root)
            elif _on == GoveeBleFrame.Command.secret_read:
                pass
                self._raw_body = self._io.read_bytes_full()
                _io__raw_body = KaitaiStream(BytesIO(self._raw_body))
                self.body = GoveeBleFrame.SecretReadReply(_io__raw_body, self, self._root)
            elif _on == GoveeBleFrame.Command.soft_version:
                pass
                self._raw_body = self._io.read_bytes_full()
                _io__raw_body = KaitaiStream(BytesIO(self._raw_body))
                self.body = GoveeBleFrame.SoftVersionRead(_io__raw_body, self, self._root)
            elif _on == GoveeBleFrame.Command.switch:
                pass
                self._raw_body = self._io.read_bytes_full()
                _io__raw_body = KaitaiStream(BytesIO(self._raw_body))
                self.body = GoveeBleFrame.SwitchReadReply(_io__raw_body, self, self._root)
            else:
                pass
                self.body = self._io.read_bytes_full()


        def _fetch_instances(self):
            pass
            _on = self.command
            if _on == GoveeBleFrame.Command.brightness:
                pass
                self.body._fetch_instances()
            elif _on == GoveeBleFrame.Command.bulb_string_color_read:
                pass
                self.body._fetch_instances()
            elif _on == GoveeBleFrame.Command.compose_light_switch:
                pass
                self.body._fetch_instances()
            elif _on == GoveeBleFrame.Command.device_info:
                pass
                self.body._fetch_instances()
            elif _on == GoveeBleFrame.Command.gradual_wifi_ble:
                pass
                self.body._fetch_instances()
            elif _on == GoveeBleFrame.Command.ic_num:
                pass
                self.body._fetch_instances()
            elif _on == GoveeBleFrame.Command.local_color_read:
                pass
                self.body._fetch_instances()
            elif _on == GoveeBleFrame.Command.mode:
                pass
                self.body._fetch_instances()
            elif _on == GoveeBleFrame.Command.plug_spec:
                pass
                self.body._fetch_instances()
            elif _on == GoveeBleFrame.Command.secret_read:
                pass
                self.body._fetch_instances()
            elif _on == GoveeBleFrame.Command.soft_version:
                pass
                self.body._fetch_instances()
            elif _on == GoveeBleFrame.Command.switch:
                pass
                self.body._fetch_instances()
            else:
                pass


    class ReadRequest(KaitaiStruct):
        """0xAA read REQUEST body = the bytes after the 0xAA header: [command][selector...]. The library keeps the
        0xAA byte, the zero padding through byte 18 and the XOR BCC at byte 19 in code. Every app read frame is
        BleUtils.Companion.p(0xAA, getCommandType(), ext): byte 1 = command, ext copied from byte 2 only when
        non-null and non-empty, the rest zero, BCC at 19 (base-classes10/com/govee/base2kt/utils/BleUtils.java:1030-1041;
        generate20Bytes :1376-1378). Legacy controllers pass AbsSingleController.p()
        (base-classes10/com/govee/base2light/ble/controller/AbsSingleController.java:17-20, default null :54-56;
        read path AbsController.java:77 getValue -> f()); kt controllers pass their ctor ext
        (base-classes11/com/govee/base2light/kt/ble/AbsController.java:42-45), truncated to 17 bytes (q() :47-60).
        Selectors as the app sends them (body hex; legacy = base-classes10/com/govee/base2light/ble/controller/,
        kt = base-classes11/com/govee/base2light/kt/general_controller/):
          01       switch. EMPTY: legacy SwitchController() has no p() (SwitchController.java:9-11);
                   kt ControllerSwitch.Companion.b -> ext null (ControllerSwitch.java:38-47,69-71).
          04       brightness. EMPTY: legacy BrightnessController() (BrightnessController.java:10-12, no p());
                   kt ControllerBrightness.Companion.b -> ext null (ControllerBrightness.java:40-48,69-71).
          05 01    mode: legacy AbsModeController.p() = {1} (AbsModeController.java:34-37); kt ControllerMode.Companion.b
                   -> synthetic ctor mask 2 -> default ext {1} (ControllerMode.java:45-47,107-109);
                   ModeControllerNoEvent read (ModeControllerNoEvent.java:37-38). Only bare aa 05: the H60A0
                   compose ModeController.Companion.a, ext null
                   (split_h60ax/com/govee/pact_h60a0/ble/controller/compose/ModeController.java:26-28,38-44).
          06       soft version. EMPTY: legacy SoftVersionController() (SoftVersionController.java:9-11, no p());
                   kt ControllerSoftVersion() -> ext byte[0] (ControllerSoftVersion.java:16-18,27-29).
          07 ss    device info, ss: 02 SN (SnController.java:37-38), 03 hard version (HardVersionController.java:28-29;
                   kt ControllerHardVersion.java:28 default {3}), 04 soft version (SoftVersionInDeviceInfoController.java:18-19),
                   07 DSP (DspVersionInDeviceInfoController.java:20-21), 0A / 0B MCU sw / hw
                   (McuSoftVersionControllerV1.java:32-33, McuHardVersionControllerV1.java:32-33),
                   10 basic info (BasicInfoController.java:34-35), 11 basic Wi-Fi info (BasicWifiInfoController.java:42-43).
                   These are the selectors the app's controllers define. Which ones a live path still
                   sends, and which ones a given firmware answers, is not recorded here; 07 10 / 07 11 in
                   particular have no confirmed live sender or firmware handler.
          36       H6047 bar switch. EMPTY: ComposeLightHeartController extends HeartController, no p()
                   (split_h6047/com/govee/h6047/ble/controller/ComposeLightHeartController.java:8-11);
                   OnOffController4H6048.a -> ext byte[0] (split_h6047/com/govee/h6047/newdetail/control/OnOffController4H6048.java:22-23,40).
          40       IC / segment count. EMPTY: kt ControllerIcNum read ctor (false, null) (ControllerIcNum.java:79,165-175);
                   kt ControllerOnlyReadIcSegmentNum() ext null (ControllerOnlyReadIcSegmentNum.java:52-58);
                   legacy IcNumController (IcNumController.java:8, AbsOnlyReadSingleController, no p()).
          41       EMPTY: kt power-on memory ControllerOnOffMemory.Companion.c -> ext null (ControllerOnOffMemory.java:49-51,119-121)
                   and the H6062 section-count read (split_h6062/com/govee/striplight/newdetail/ControllerIcSegmentNum.java:40-41,61-67);
                   the device decides which reply a bare aa 41 gets.
          41 02    legacy power-on memory OnOffMemoryController(type != -1, false): p() = {2} (OnOffMemoryController.java:18-22,63-66).
          a3       gradual. EMPTY: kt ControllerGradual4BleWifi.Companion.b -> (ext null, read) (ControllerGradual4BleWifi.java:47-48,104-114);
                   legacy GradualChangeBleWifiController(), no p() (GradualChangeBleWifiController.java:7-8,22-23).
          a5 gg    colour group with brightness, gg = 1-based group number (callers loop 1..maxGroup, e.g.
                   split_rgbiclight/com/govee/rgbiclight/VM4Light.java:937-958): kt Controller4ColorInfoByGroup.Companion.g / m
                   -> ext {(byte) group} (Controller4ColorInfoByGroup.java:112-113,134-136,197-199,210-211).
          a2 gg    colour group, colour only, gg 1-based: Controller4ColorInfoByGroup.Companion.j (Controller4ColorInfoByGroup.java:123-124).
          b1       secret. EMPTY: legacy SecretKeyController(byte) read, no p() (SecretKeyController.java:13-20).
          b3       plug spec. EMPTY: h5080 SpecController, no p() (split_plugnew/com/govee/h5080/ble/controller/SpecController.java:6-13).
        Writing: give the stream exactly 1 + len(selector) bytes. selector is size-eos, so the writer raises
        ConsistencyError when the stream does not end right after it. Reading: a body sliced from a captured
        20-byte frame (frame[1:19]) keeps the zero padding inside selector. Every selector above is non-zero, so
        trailing 00 bytes there are padding. command uses enum command: every command above is in it; a command
        byte missing from the enum still round-trips in Python (resolve_enum keeps the int) but reads as null in Java.
        """
        def __init__(self, _io, _parent=None, _root=None):
            super(GoveeBleFrame.ReadRequest, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.command = KaitaiStream.resolve_enum(GoveeBleFrame.Command, self._io.read_u1())
            self.selector = self._io.read_bytes_full()
            _ = self.selector
            if not len(_) <= 17:
                raise kaitaistruct.ValidationExprError(self.selector, self._io, u"/types/read_request/seq/1")


        def _fetch_instances(self):
            pass


    class RgbSceneValue(KaitaiStruct):
        """dialect-A rgb library scene (ScenesRgb.isValidProtocolBytes) = decoded scenceParam verbatim.
        CONFIG-TABLE-DRIVEN: byte0 indexes a hardcoded config e(byte0) = [_, mode, color_count]
        (ScenesRgb.e — NOT present in the value bytes) which selects the body shape:
          mode 1: size == 2 + effect_count*(color_count + 5)                     (effect_count @ byte1)
          mode 0: size == effect_count*5 + 3 + color_count*color_num             (color_num @ byte[eff*5+2])
        Because mode/color_count come from the external table, the body is NOT self-describing from bytes
        alone; modelled as byte0 + opaque remainder. See GOVEE_BLE_GATT_PROTOCOL.md §4.4.
        """
        def __init__(self, _io, _parent=None, _root=None):
            super(GoveeBleFrame.RgbSceneValue, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.config_tag = self._io.read_u1()
            self.rest = self._io.read_bytes_full()


        def _fetch_instances(self):
            pass


    class RgbicEffect(KaitaiStruct):
        def __init__(self, _io, _parent=None, _root=None):
            super(GoveeBleFrame.RgbicEffect, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.sub_len = self._io.read_u1()
            self._raw_record = self._io.read_bytes(self.sub_len)
            _io__raw_record = KaitaiStream(BytesIO(self._raw_record))
            self.record = GoveeBleFrame.RgbicEffectRecord(_io__raw_record, self, self._root)


        def _fetch_instances(self):
            pass
            self.record._fetch_instances()


    class RgbicEffectRecord(KaitaiStruct):
        """One rgbic effect record — FULLY interpreted (source of truth = ParamsV2.RgbICEffect parser :967 /
        serializer k() :706; ScenesRgbIC.f :3337 is only a length-validator, which is why an earlier draft
        mislabeled these bytes). Layout: style byte (packed nibbles) · mode + mode-dependent 2-byte value ·
        a brightness block (bright_count × 6-byte BrightnessEffect) · a ColorEffect (colour-IC byte + speed +
        duration + the RGB palette = the ACTUAL colours) · then InAreaMove(3) + AreaMove(4) movement blocks.
        Verified: parses all 327 dialect-A rgbic catalog scenes (H6047/H61A8/H6641) with exact consumption;
        colours decode correctly (e.g. H6047 "Action" → red then blue).
        """
        def __init__(self, _io, _parent=None, _root=None):
            super(GoveeBleFrame.RgbicEffectRecord, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.style = self._io.read_u1()
            self.mode = self._io.read_u1()
            self.mode_val = self._io.read_u2le()
            self.bright_algo = self._io.read_u1()
            self.bright_count = self._io.read_u1()
            self.brightness_effects = []
            for i in range(self.bright_count):
                self.brightness_effects.append(GoveeBleFrame.BrightnessEffect(self._io, self, self._root))

            self.color_ic = self._io.read_u1()
            self.speed = self._io.read_u1()
            self.duration = self._io.read_u1()
            self.color_count = self._io.read_u1()
            self.colors = []
            for i in range(self.color_count):
                self.colors.append(GoveeBleFrame.ScRgb(self._io, self, self._root))

            self.in_area_move = GoveeBleFrame.InAreaMove(self._io, self, self._root)
            self.area_move = GoveeBleFrame.AreaMove(self._io, self, self._root)


        def _fetch_instances(self):
            pass
            for i in range(len(self.brightness_effects)):
                pass
                self.brightness_effects[i]._fetch_instances()

            for i in range(len(self.colors)):
                pass
                self.colors[i]._fetch_instances()

            self.in_area_move._fetch_instances()
            self.area_move._fetch_instances()


    class RgbicSceneValue(KaitaiStruct):
        """dialect-A rgbic library scene (ScenesRgbIC.isValidProtocolBytes) = decoded scenceParam VERBATIM.
        effect_count, then that many length-prefixed effect records (fully structured — see
        rgbic_effect_record). Verified: parses all 57 H6047 + 135 H61A8 catalog scenes with exact consumption.
        """
        def __init__(self, _io, _parent=None, _root=None):
            super(GoveeBleFrame.RgbicSceneValue, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.effect_count = self._io.read_u1()
            self.effects = []
            for i in range(self.effect_count):
                self.effects.append(GoveeBleFrame.RgbicEffect(self._io, self, self._root))



        def _fetch_instances(self):
            pass
            for i in range(len(self.effects)):
                pass
                self.effects[i]._fetch_instances()



    class ScRgb(KaitaiStruct):
        def __init__(self, _io, _parent=None, _root=None):
            super(GoveeBleFrame.ScRgb, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.r = self._io.read_u1()
            self.g = self._io.read_u1()
            self.b = self._io.read_u1()


        def _fetch_instances(self):
            pass


    class ScenePayload(KaitaiStruct):
        def __init__(self, _io, _parent=None, _root=None):
            super(GoveeBleFrame.ScenePayload, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.effect = self._io.read_u2le()


        def _fetch_instances(self):
            pass


    class ScenesGraffitiGroup(KaitaiStruct):
        def __init__(self, _io, _parent=None, _root=None):
            super(GoveeBleFrame.ScenesGraffitiGroup, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.color_count = self._io.read_u1()
            self.r = self._io.read_u1()
            self.g = self._io.read_u1()
            self.b = self._io.read_u1()
            self.pixel_indices = []
            for i in range(self.color_count):
                self.pixel_indices.append(self._io.read_u1())



        def _fetch_instances(self):
            pass
            for i in range(len(self.pixel_indices)):
                pass



    class ScenesGraffitiValue(KaitaiStruct):
        """dialect-A graffiti library scene. Wire value = decode(scenceParam)[2:] (strips the 0x01 header +
        effect_hi byte; ScenesOp.n():483). Structure = ParamsV1.a() / DiyProtocolParser.parserParamsV1:1345 —
        effect_lo, speed, brightness, background RGB, seg_count, then seg_count colour groups. Verified:
        parses all 25 H6052 sceneType-3 catalog scenes with exact consumption.
        """
        def __init__(self, _io, _parent=None, _root=None):
            super(GoveeBleFrame.ScenesGraffitiValue, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.effect_lo = self._io.read_u1()
            self.speed = self._io.read_u1()
            self.brightness = self._io.read_u1()
            self.bg_r = self._io.read_u1()
            self.bg_g = self._io.read_u1()
            self.bg_b = self._io.read_u1()
            self.seg_count = self._io.read_u1()
            self.groups = []
            for i in range(self.seg_count):
                self.groups.append(GoveeBleFrame.ScenesGraffitiGroup(self._io, self, self._root))



        def _fetch_instances(self):
            pass
            for i in range(len(self.groups)):
                pass
                self.groups[i]._fetch_instances()



    class SecretKeyWrite(KaitaiStruct):
        """33 B2 account-lock secret write: body = the 8-byte secret, NO selector byte. Every builder writes Encode.decryByBase64(secretCode) as the whole ext array: SecretKeyController.q (base-classes10/com/govee/base2light/ble/controller/SecretKeyController.java:22-26,71-78; command SINGLE_CHECK_SECRET_KEY = -78, BleProtocolConstants.java:110), h5086 SecretController.d (split_plugnew/com/govee/h5086/ble/controller/SecretController.java:66-75,86-88), h61d3 SecretController.d (split_h61d3/com/govee/h61d3/ble/controller/SecretController.java:67-76,87-89) and Controller4Secret.a (base-classes12/com/govee/base2light/vm4sku2op/Controller4Secret.java:28-46; Controller.x = -78). The secretCode is the base64 of the 8 bytes the AA B1 reply carried after its 0x01 selector (SecretKeyController.java:57-62; h5086 SecretController.java:41-44), so it decodes to 8 bytes. The write path does not check the length. The AA B1 reply is secret_read_reply."""
        def __init__(self, _io, _parent=None, _root=None):
            super(GoveeBleFrame.SecretKeyWrite, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.secret = self._io.read_bytes(8)


        def _fetch_instances(self):
            pass


    class SecretReadReply(KaitaiStruct):
        """AA B1 reply (SecretKeyController.parseValidBytes): selector (0x01 = valid) then the 8-byte account-lock secret. The 33 B2 write sends the raw 8 bytes (no selector). This is the account-lock, NOT the wire cipher."""
        def __init__(self, _io, _parent=None, _root=None):
            super(GoveeBleFrame.SecretReadReply, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.selector = self._io.read_u1()
            self.secret = self._io.read_bytes(8)
            self.pad = self._io.read_bytes_full()


        def _fetch_instances(self):
            pass


    class SingleCommand(KaitaiStruct):
        def __init__(self, _io, _parent=None, _root=None):
            super(GoveeBleFrame.SingleCommand, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.command = KaitaiStream.resolve_enum(GoveeBleFrame.Command, self._io.read_u1())
            _on = self.command
            if _on == GoveeBleFrame.Command.brightness:
                pass
                self._raw_params = self._io.read_bytes_full()
                _io__raw_params = KaitaiStream(BytesIO(self._raw_params))
                self.params = GoveeBleFrame.BrightnessPayload(_io__raw_params, self, self._root)
            elif _on == GoveeBleFrame.Command.mode:
                pass
                self._raw_params = self._io.read_bytes_full()
                _io__raw_params = KaitaiStream(BytesIO(self._raw_params))
                self.params = GoveeBleFrame.ModePayload(_io__raw_params, self, self._root)
            elif _on == GoveeBleFrame.Command.plug_sync_time:
                pass
                self._raw_params = self._io.read_bytes_full()
                _io__raw_params = KaitaiStream(BytesIO(self._raw_params))
                self.params = GoveeBleFrame.PlugSyncTimePayload(_io__raw_params, self, self._root)
            elif _on == GoveeBleFrame.Command.switch:
                pass
                self._raw_params = self._io.read_bytes_full()
                _io__raw_params = KaitaiStream(BytesIO(self._raw_params))
                self.params = GoveeBleFrame.SwitchPayload(_io__raw_params, self, self._root)
            elif _on == GoveeBleFrame.Command.sync_time:
                pass
                self._raw_params = self._io.read_bytes_full()
                _io__raw_params = KaitaiStream(BytesIO(self._raw_params))
                self.params = GoveeBleFrame.SyncTimePayload(_io__raw_params, self, self._root)
            else:
                pass
                self.params = self._io.read_bytes_full()


        def _fetch_instances(self):
            pass
            _on = self.command
            if _on == GoveeBleFrame.Command.brightness:
                pass
                self.params._fetch_instances()
            elif _on == GoveeBleFrame.Command.mode:
                pass
                self.params._fetch_instances()
            elif _on == GoveeBleFrame.Command.plug_sync_time:
                pass
                self.params._fetch_instances()
            elif _on == GoveeBleFrame.Command.switch:
                pass
                self.params._fetch_instances()
            elif _on == GoveeBleFrame.Command.sync_time:
                pass
                self.params._fetch_instances()
            else:
                pass


    class SoftVersionRead(KaitaiStruct):
        def __init__(self, _io, _parent=None, _root=None):
            super(GoveeBleFrame.SoftVersionRead, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.version = (KaitaiStream.bytes_terminate(self._io.read_bytes_full(), 0, False)).decode(u"UTF-8")


        def _fetch_instances(self):
            pass


    class StatusBrightness(KaitaiStruct):
        def __init__(self, _io, _parent=None, _root=None):
            super(GoveeBleFrame.StatusBrightness, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.brightness = self._io.read_u1()


        def _fetch_instances(self):
            pass


    class StatusDeviceInfo(KaitaiStruct):
        """0xAC status TLV 0x07 as the app's status parser reads it (Compose4BaseInfoSingleRead.w:370-437, Govee
        Home 7.7.02): ONLY selector 0x10 (basic) and 0x11 (Wi-Fi) are decoded. Every other selector, including
        0x02 (SN) and 0x03 (hard version), which the single-frame aa 07 reply does handle, is logged and ignored
        with no body read (w:376-382), so it stays raw here. Standalone: status_tlv's 0x07 case keeps
        device_info_read for existing readers, since switching it changes which 0x07 blocks such a reader
        fails to parse.
        """
        def __init__(self, _io, _parent=None, _root=None):
            super(GoveeBleFrame.StatusDeviceInfo, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.selector = self._io.read_u1()
            _on = self.selector
            if _on == 16:
                pass
                self._raw_info = self._io.read_bytes_full()
                _io__raw_info = KaitaiStream(BytesIO(self._raw_info))
                self.info = GoveeBleFrame.DeviceInfoBasic(_io__raw_info, self, self._root)
            elif _on == 17:
                pass
                self._raw_info = self._io.read_bytes_full()
                _io__raw_info = KaitaiStream(BytesIO(self._raw_info))
                self.info = GoveeBleFrame.DeviceInfoWifi(_io__raw_info, self, self._root)
            else:
                pass
                self.info = self._io.read_bytes_full()


        def _fetch_instances(self):
            pass
            _on = self.selector
            if _on == 16:
                pass
                self.info._fetch_instances()
            elif _on == 17:
                pass
                self.info._fetch_instances()
            else:
                pass


    class StatusReply(KaitaiStruct):
        """Reassembled 0xAC status reply = a sequence of [type, len, value] TLVs. The buffer is de-chunked from
        the 0xAC frames. Trimmed to the first chunk's lastLen as the app does (see multi_ac) it ends exactly
        at the last TLV; a client that joins the whole zero-padded last chunk instead gets TRAILING ZERO
        PADDING. Terminate on either: no real TLV type is 0x00, and the walker
        (Compose4BaseInfoSingleRead.u:299-326) also stops once fewer than 2 bytes remain. So parse until a
        type-0 sentinel or EOF — repeat: eos would instead throw on the padding (a lone trailing 0 byte has
        no room for its len). The trailing type-0 element, if present, is the padding sentinel (no len/value).
        """
        def __init__(self, _io, _parent=None, _root=None):
            super(GoveeBleFrame.StatusReply, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.tlvs = []
            i = 0
            while True:
                _ = GoveeBleFrame.StatusTlv(self._io, self, self._root)
                self.tlvs.append(_)
                if  ((_.type == 0) or (self._io.is_eof())) :
                    break
                i += 1


        def _fetch_instances(self):
            pass
            for i in range(len(self.tlvs)):
                pass
                self.tlvs[i]._fetch_instances()



    class StatusSegInfo(KaitaiStruct):
        """0x41 seg/IC info (VM4LightH60A6.o:99 reads byte1)."""
        def __init__(self, _io, _parent=None, _root=None):
            super(GoveeBleFrame.StatusSegInfo, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.b0 = self._io.read_u1()
            self.ic_or_seg = self._io.read_u1()


        def _fetch_instances(self):
            pass


    class StatusSwitch(KaitaiStruct):
        """0xAC status TLV 0x01: power. The status parser decodes on as state != 0 (Compose4BaseInfoSingleRead.u: bArr2[0] != 0, base-classes11/com/govee/base2light/pact/newdetail/ble/controller/Compose4BaseInfoSingleRead.java:286-288): use is_on. Screens that take connect-time power from here and send no aa 01 read are devices.yaml client_profile.power_read: ac_status (H60A4/H60A6, H6039/H6038/H6048, H703A/H703B)."""
        def __init__(self, _io, _parent=None, _root=None):
            super(GoveeBleFrame.StatusSwitch, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.state = self._io.read_u1()


        def _fetch_instances(self):
            pass

        @property
        def is_on(self):
            """Power as Compose4BaseInfoSingleRead.u decodes TLV 0x01 (!= 0). Differs from the aa 01 control-screen rule (switch_read_reply.light_is_on, == 1) only for a byte >= 2."""
            if hasattr(self, '_m_is_on'):
                return self._m_is_on

            self._m_is_on = self.state != 0
            return getattr(self, '_m_is_on', None)


    class StatusTlv(KaitaiStruct):
        def __init__(self, _io, _parent=None, _root=None):
            super(GoveeBleFrame.StatusTlv, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.type = self._io.read_u1()
            if self.type != 0:
                pass
                self.len = self._io.read_u1()
                if not self.len <= 127:
                    raise kaitaistruct.ValidationGreaterThanError(127, self.len, self._io, u"/types/status_tlv/seq/1")

            if self.type != 0:
                pass
                _on = self.type
                if _on == 1:
                    pass
                    self._raw_value = self._io.read_bytes(self.len)
                    _io__raw_value = KaitaiStream(BytesIO(self._raw_value))
                    self.value = GoveeBleFrame.StatusSwitch(_io__raw_value, self, self._root)
                elif _on == 165:
                    pass
                    self._raw_value = self._io.read_bytes(self.len)
                    _io__raw_value = KaitaiStream(BytesIO(self._raw_value))
                    self.value = GoveeBleFrame.ColorGroupStatus(_io__raw_value, self, self._root)
                elif _on == 4:
                    pass
                    self._raw_value = self._io.read_bytes(self.len)
                    _io__raw_value = KaitaiStream(BytesIO(self._raw_value))
                    self.value = GoveeBleFrame.StatusBrightness(_io__raw_value, self, self._root)
                elif _on == 48:
                    pass
                    self._raw_value = self._io.read_bytes(self.len)
                    _io__raw_value = KaitaiStream(BytesIO(self._raw_value))
                    self.value = GoveeBleFrame.StatusZone(_io__raw_value, self, self._root)
                elif _on == 5:
                    pass
                    self._raw_value = self._io.read_bytes(self.len)
                    _io__raw_value = KaitaiStream(BytesIO(self._raw_value))
                    self.value = GoveeBleFrame.ModeStatus(_io__raw_value, self, self._root)
                elif _on == 65:
                    pass
                    self._raw_value = self._io.read_bytes(self.len)
                    _io__raw_value = KaitaiStream(BytesIO(self._raw_value))
                    self.value = GoveeBleFrame.StatusSegInfo(_io__raw_value, self, self._root)
                elif _on == 7:
                    pass
                    self._raw_value = self._io.read_bytes(self.len)
                    _io__raw_value = KaitaiStream(BytesIO(self._raw_value))
                    self.value = GoveeBleFrame.DeviceInfoRead(_io__raw_value, self, self._root)
                else:
                    pass
                    self.value = self._io.read_bytes(self.len)



        def _fetch_instances(self):
            pass
            if self.type != 0:
                pass

            if self.type != 0:
                pass
                _on = self.type
                if _on == 1:
                    pass
                    self.value._fetch_instances()
                elif _on == 165:
                    pass
                    self.value._fetch_instances()
                elif _on == 4:
                    pass
                    self.value._fetch_instances()
                elif _on == 48:
                    pass
                    self.value._fetch_instances()
                elif _on == 5:
                    pass
                    self.value._fetch_instances()
                elif _on == 65:
                    pass
                    self.value._fetch_instances()
                elif _on == 7:
                    pass
                    self.value._fetch_instances()
                else:
                    pass



    class StatusZone(KaitaiStruct):
        """0x30 in the 0xAC reply: zone0 = bit0 of byte0, zone1 = bit0 of byte1 (VM4LightH60A6.o:103)."""
        def __init__(self, _io, _parent=None, _root=None):
            super(GoveeBleFrame.StatusZone, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.zone_a = self._io.read_u1()
            self.zone_b = self._io.read_u1()


        def _fetch_instances(self):
            pass


    class SwitchPayload(KaitaiStruct):
        def __init__(self, _io, _parent=None, _root=None):
            super(GoveeBleFrame.SwitchPayload, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.state = self._io.read_u1()


        def _fetch_instances(self):
            pass


    class SwitchReadReply(KaitaiStruct):
        """AA 01 reply: state @ body[0]. LIGHTS: every curated control screen that reads it decodes on as body[0] == 1 (ControllerSwitch.Companion.c, base-classes11/com/govee/base2light/kt/general_controller/ControllerSwitch.java:32), at connect and in the aa 01 heartbeat (ShareVM.N3 frame[2] == 1, ShareVM.java:1008): use light_is_on. The legacy SwitchController.parseValidBytes reads it as != 0 (SwitchController.java:30). No curated or profiled control screen reaches it, but older detail screens for legacy goodsTypes do (e.g. the dreamcolorlightv1 adjust/v1-v2 connect reads, BleOpV1.java:735, BleOpV2.java:123, BleOpV3.java:709), as do scene / IoT command paths (BleSwitchCmdBuilderV1); the two agree for 0/1 and differ only for a byte >= 2. Which message carries a family's power (this reply, or the 0xAC status TLV 0x01) is devices.yaml client_profile.power_read. PLUGS (h5080 SwitchControllerV2): this SAME 0x01 reply is a RELAY BITMASK (bit i = relay i on), not boolean — interpret per device."""
        def __init__(self, _io, _parent=None, _root=None):
            super(GoveeBleFrame.SwitchReadReply, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.state = self._io.read_u1()


        def _fetch_instances(self):
            pass

        @property
        def light_is_on(self):
            """Lights: power as the curated control screens decode it (ControllerSwitch, == 1). Not for plugs, whose state is a relay mask."""
            if hasattr(self, '_m_light_is_on'):
                return self._m_light_is_on

            self._m_light_is_on = self.state == 1
            return getattr(self, '_m_light_is_on', None)


    class SyncTimePayload(KaitaiStruct):
        """Lights, command 0x09."""
        def __init__(self, _io, _parent=None, _root=None):
            super(GoveeBleFrame.SyncTimePayload, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.hour = self._io.read_u1()
            self.minute = self._io.read_u1()
            self.second = self._io.read_u1()
            self.week = self._io.read_u1()
            self.marker = self._io.read_u1()
            self.tz_hour = self._io.read_s1()
            self.tz_min = self._io.read_s1()


        def _fetch_instances(self):
            pass


    class Version3(KaitaiStruct):
        """3-byte firmware version rendered X.YY.ZZ (major . %02d . %02d); BasicInfoController.u:73."""
        def __init__(self, _io, _parent=None, _root=None):
            super(GoveeBleFrame.Version3, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.major = self._io.read_u1()
            self.minor = self._io.read_u1()
            self.patch = self._io.read_u1()


        def _fetch_instances(self):
            pass


    class WorryFreeAtNightWrite(KaitaiStruct):
        """33 36 H6057 worry-free-at-night write: body = WorryFreeAtNightBean.o() = [state, open hour, open minute, close hour, close minute, sensitivity, brightness] (base-classes10/com/govee/base2light/ble/controller/WorryFreeAtNightBean.java:90-92; getters g/d/e/b/c/f/a return fields a/b/c/d/e/f/g, :34-59), sent as ControllerSingle.Companion.c(54, bytes) (split_hollowlamp/com/govee/h6057/origin/newDetail/H6057SettingActivity.java:846-850; legacy split_hollowlamp/com/govee/h6057/origin/ble/WorryFreeAtNightController.java:76-78,87-93). A 7-byte body under 33 36 is this, not a bar switch. Catalogue: build_worry_free_at_night."""
        def __init__(self, _io, _parent=None, _root=None):
            super(GoveeBleFrame.WorryFreeAtNightWrite, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.state = self._io.read_u1()
            self.open_hour = self._io.read_u1()
            self.open_min = self._io.read_u1()
            self.close_hour = self._io.read_u1()
            self.close_min = self._io.read_u1()
            self.sensitivity = self._io.read_u1()
            self.brightness = self._io.read_u1()


        def _fetch_instances(self):
            pass


    class ZonePowerWrite(KaitaiStruct):
        """33 30 per-zone on/off write (pact_h60a0 family): body = [zone, state]. Built as Controller4ExtBytes.Companion.f(48, [zone, state]) (base-classes11/com/govee/base2light/kt/general_controller/Controller4ExtBytes.java:121-124). H60A6 VM4LightH60A6.y5 (split_h60ax/com/govee/pact_h60a0/adjust/h60a6/VM4LightH60A6.java:1373-1376) writes [!isFirst ? 1 : 0, on], called with isFirst=false (zone 1, DelegateHeaderH60A6.java:442) and isFirst=true (zone 0, DelegateHeaderH60A6.java:470); H1270 VM4LightH1270.F5 (h1270/VM4LightH1270.java:672-675) and H1250 VM4LightH1250.n5 (h1250/VM4LightH1250.java:911-914) are identical; H12D0 VM4LightH12D0.B5 (h12d0/VM4LightH12D0.java:618-622) takes the caller's array [2|1|0, on] (DelegateHeaderH12D0.java:580,608,636), so H12D0 has three zones. The snapshot path writes one frame per zone, [0, zone0_on] then [1, zone1_on] (VM4LightH60A6.java:1215-1216; H12D0 adds [2, zone2_on], VM4LightH12D0.java:905-907). The zone states come back in the 0xAC status TLV 0x30 (status_zone) and the EE 30 push (notify_switch_zone). The frame for [1, 0] is byte-identical to light_direction_write value=1 (33 30 01 00 .. 02): pick by device family."""
        def __init__(self, _io, _parent=None, _root=None):
            super(GoveeBleFrame.ZonePowerWrite, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.zone = self._io.read_u1()
            self.state = self._io.read_u1()


        def _fetch_instances(self):
            pass



