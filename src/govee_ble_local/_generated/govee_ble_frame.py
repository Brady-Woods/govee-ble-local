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
        """AA 36 reply (h6047 ComposeLightController): [left,right] on/off (bArr[0]==1 / bArr[1]==1). The 33 36 write uses the same [left,right]."""
        def __init__(self, _io, _parent=None, _root=None):
            super(GoveeBleFrame.BarSwitchRead, self).__init__(_io)
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
        """Mode 0x15 CCT read reply. kelvin = u2be at frame bytes 4-5 (e.g. 0x0A8C=2700, 0x1964=6500); NO FF FF FF white point (unlike the write, which is [op, FF FF FF, kHi, kLo, ...]). Source: SubModeColorV2.parse / ComposeChange2ColorMode.result."""
        def __init__(self, _io, _parent=None, _root=None):
            super(GoveeBleFrame.CctReadReply, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.op = self._io.read_u1()
            self.kelvin = self._io.read_u2be()
            self.trailing = self._io.read_bytes_full()


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
        """One 0xA5 group of the per-segment colour reply: group_index then record_count records. record_count
        is supplied by the caller (= 4, except the last group = N - 4*(groups-1)); it is NOT the TLV length.
        Records are 4-byte [brightness,R,G,B] on part-brightness SKUs; on SKUs without it (e.g. H6641 ".p"
        firmware path) they are 3-byte [R,G,B] with no brightness — parse those with color_group_record_rgb.
        """
        def __init__(self, record_count, _io, _parent=None, _root=None):
            super(GoveeBleFrame.ColorGroupRead, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self.record_count = record_count
            self._read()

        def _read(self):
            self.group_index = self._io.read_u1()
            self.records = []
            for i in range(self.record_count):
                self.records.append(GoveeBleFrame.ColorGroupRecord(self._io, self, self._root))



        def _fetch_instances(self):
            pass
            for i in range(len(self.records)):
                pass
                self.records[i]._fetch_instances()



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


    class DeviceInfoRead(KaitaiStruct):
        """AA 07 reply (BasicInfoController / BasicWifiInfoController / SnController). selector @ body[0] picks the
        body. 3-byte version fields render as X.YY.ZZ (major . %02d . %02d).
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
            self.on = self._io.read_u1()
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


    class ModeColor0dReport(KaitaiStruct):
        """Mode 0x05 sub-mode 0x0D colour read-back. Frame = [proType][05][0D][body…]; this type = the body
        (bytes after the 0x0D sub-mode byte). Body interpretation is DEVICE-SPECIFIC — chosen by the
        IParseStrategy each device's *InfoDetail installs on its SubMode4Color:
          * H6052 (tablelampv1, CUSTOM strategy) => [R, G, B]: a SINGLE colour (ColorUtils.G(vb0,vb1,vb2))
            fanned across the device's zones (getColorSize: H6052=2). This resolves the old "0x0d is write-only"
            note — H6052 DOES read colour back, as these 3 bytes. Source: H6052InfoDetail.parseModeValidBytes
            :323-346 (validBytes[0]==0x0D -> colour strategy on validBytes[1:]) + :141-147.
          * DEFAULT SubMode4Color strategy (most other families) => [gradual_flag, kelvin u16-be], NOT RGB
            (iParseStrategy: q(vb0==1); s(getSignedShort(vb1,vb2))). For those devices reinterpret r as the
            gradual flag and g,b as the big-endian kelvin.
        Modelled as the H6052 RGB form (the curated device). Trailing frame bytes are zero padding (unparsed).
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
        (0x15 color / 0x04 scene / 0x13 music / 0x0d h60a6-color). Disambiguate by value: 0x01 => request;
        a known sub-mode => reply.
        - 0x15 CCT reply => cct_read_reply: [op=01][kHi][kLo], kelvin big-endian @ frame bytes 4-5; DROPS
          the FF FF FF white point the 0x33 write carries.
        - 0x0d (and 0x15 on the LEGACY Mode/SubModeColorV1 stack): parse reads ONLY op/gradual (bArr[0]==1)
          — no kelvin/RGB decode; 0x0d is effectively write-only. Kelvin is decoded only by the compose
          stack's 0x15 path (cct_read_reply). So no distinct 0x0d reply body is modelled.
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
        Reply: a burst of 0xAC frames, tag @ byte1; first chunk 12 data bytes @ offset 7,
        subsequent 17 data bytes @ offset 2, terminator tag 0xFF. Reassembled buffer is a
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
        Interpret per device (flags_a = that generic flags byte).
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
            elif _on == GoveeBleFrame.Command.switch:
                pass
                self.body._fetch_instances()
            else:
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


    class StatusReply(KaitaiStruct):
        """Reassembled 0xAC status reply = a sequence of [type, len, value] TLVs. Value left opaque here; 0xA5 -> color_group_read, 0x30 -> two zone on/off bits, 0x41 -> seg/IC info."""
        def __init__(self, _io, _parent=None, _root=None):
            super(GoveeBleFrame.StatusReply, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.tlvs = []
            i = 0
            while not self._io.is_eof():
                self.tlvs.append(GoveeBleFrame.StatusTlv(self._io, self, self._root))
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
        def __init__(self, _io, _parent=None, _root=None):
            super(GoveeBleFrame.StatusSwitch, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.on = self._io.read_u1()


        def _fetch_instances(self):
            pass


    class StatusTlv(KaitaiStruct):
        def __init__(self, _io, _parent=None, _root=None):
            super(GoveeBleFrame.StatusTlv, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.type = self._io.read_u1()
            self.len = self._io.read_u1()
            _on = self.type
            if _on == 1:
                pass
                self._raw_value = self._io.read_bytes(self.len)
                _io__raw_value = KaitaiStream(BytesIO(self._raw_value))
                self.value = GoveeBleFrame.StatusSwitch(_io__raw_value, self, self._root)
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
            elif _on == 65:
                pass
                self._raw_value = self._io.read_bytes(self.len)
                _io__raw_value = KaitaiStream(BytesIO(self._raw_value))
                self.value = GoveeBleFrame.StatusSegInfo(_io__raw_value, self, self._root)
            else:
                pass
                self.value = self._io.read_bytes(self.len)


        def _fetch_instances(self):
            pass
            _on = self.type
            if _on == 1:
                pass
                self.value._fetch_instances()
            elif _on == 4:
                pass
                self.value._fetch_instances()
            elif _on == 48:
                pass
                self.value._fetch_instances()
            elif _on == 65:
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
        """AA 01 reply (SwitchController.parseValidBytes): state @ body[0]. LIGHTS: 0=off else on. PLUGS (h5080 SwitchControllerV2): this SAME 0x01 reply is a RELAY BITMASK (bit i = relay i on), not boolean — interpret per device."""
        def __init__(self, _io, _parent=None, _root=None):
            super(GoveeBleFrame.SwitchReadReply, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.state = self._io.read_u1()


        def _fetch_instances(self):
            pass


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



