# This is a generated file! Please edit source .ksy file and use kaitai-struct-compiler to rebuild
# type: ignore

import kaitaistruct
from kaitaistruct import ReadWriteKaitaiStruct, KaitaiStream, BytesIO


if getattr(kaitaistruct, 'API_VERSION', (0, 9)) < (0, 11):
    raise Exception("Incompatible Kaitai Struct Python API: 0.11 or later is required, but you have %s" % (kaitaistruct.__version__))

class GoveeAdvertisement(ReadWriteKaitaiStruct):
    """Connectionless parse of a BLE scan record. The app walks AD structures `[len][type][data]` and
    reads the Govee identity/state from the manufacturer-specific AD (type 0xFF). See
    GOVEE_BLE_GATT_PROTOCOL.md §19 and base2home/pact/BleUtil.java:829 (parseBleBroadcastPact).
    
    NOTE: Govee's manufacturer-data layout is custom — a `flags` byte PRECEDES the `88 EC` marker
    (i.e. it is not a standard leading 2-byte company identifier). `manufacturer_data` therefore only
    makes sense when `is_govee` is true; for other vendors' 0xFF ADs the fields are meaningless.
    Termination: a `len == 0` structure marks the end of meaningful data (zero padding).
    """
    def __init__(self, _io=None, _parent=None, _root=None):
        super(GoveeAdvertisement, self).__init__(_io)
        self._parent = _parent
        self._root = _root or self

    def _read(self):
        self.structures = []
        i = 0
        while True:
            _t_structures = GoveeAdvertisement.AdStructure(self._io, self, self._root)
            try:
                _t_structures._read()
            finally:
                _ = _t_structures
                self.structures.append(_)
            if _.len == 0:
                break
            i += 1
        self._dirty = False


    def _fetch_instances(self):
        pass
        for i in range(len(self.structures)):
            pass
            self.structures[i]._fetch_instances()



    def _write__seq(self, io=None):
        super(GoveeAdvertisement, self)._write__seq(io)
        for i in range(len(self.structures)):
            pass
            self.structures[i]._write__seq(self._io)



    def _check(self):
        if len(self.structures) == 0:
            raise kaitaistruct.ConsistencyError(u"structures", 0, len(self.structures))
        for i in range(len(self.structures)):
            pass
            if self.structures[i]._root != self._root:
                raise kaitaistruct.ConsistencyError(u"structures", self._root, self.structures[i]._root)
            if self.structures[i]._parent != self:
                raise kaitaistruct.ConsistencyError(u"structures", self, self.structures[i]._parent)
            _ = self.structures[i]
            if (_.len == 0) != (i == len(self.structures) - 1):
                raise kaitaistruct.ConsistencyError(u"structures", i == len(self.structures) - 1, _.len == 0)

        self._dirty = False

    class AdStructure(ReadWriteKaitaiStruct):
        def __init__(self, _io=None, _parent=None, _root=None):
            super(GoveeAdvertisement.AdStructure, self).__init__(_io)
            self._parent = _parent
            self._root = _root

        def _read(self):
            self.len = self._io.read_u1()
            if self.len > 0:
                pass
                self.ad_type = self._io.read_u1()

            if self.len > 0:
                pass
                _on = self.ad_type
                if _on == 255:
                    pass
                    self._raw_data = self._io.read_bytes(self.len - 1)
                    _io__raw_data = KaitaiStream(BytesIO(self._raw_data))
                    self.data = GoveeAdvertisement.ManufacturerData(_io__raw_data, self, self._root)
                    self.data._read()
                else:
                    pass
                    self.data = self._io.read_bytes(self.len - 1)

            self._dirty = False


        def _fetch_instances(self):
            pass
            if self.len > 0:
                pass

            if self.len > 0:
                pass
                _on = self.ad_type
                if _on == 255:
                    pass
                    self.data._fetch_instances()
                else:
                    pass



        def _write__seq(self, io=None):
            super(GoveeAdvertisement.AdStructure, self)._write__seq(io)
            self._io.write_u1(self.len)
            if self.len > 0:
                pass
                self._io.write_u1(self.ad_type)

            if self.len > 0:
                pass
                _on = self.ad_type
                if _on == 255:
                    pass
                    _io__raw_data = KaitaiStream(BytesIO(bytearray(self.len - 1)))
                    self._io.add_child_stream(_io__raw_data)
                    _pos2 = self._io.pos()
                    self._io.seek(self._io.pos() + (self.len - 1))
                    def handler(parent, _io__raw_data=_io__raw_data):
                        self._raw_data = _io__raw_data.to_byte_array()
                        if len(self._raw_data) != self.len - 1:
                            raise kaitaistruct.ConsistencyError(u"raw(data)", self.len - 1, len(self._raw_data))
                        parent.write_bytes(self._raw_data)
                    _io__raw_data.write_back_handler = KaitaiStream.WriteBackHandler(_pos2, handler)
                    self.data._write__seq(_io__raw_data)
                else:
                    pass
                    self._io.write_bytes(self.data)



        def _check(self):
            if self.len > 0:
                pass

            if self.len > 0:
                pass
                _on = self.ad_type
                if _on == 255:
                    pass
                    if self.data._root != self._root:
                        raise kaitaistruct.ConsistencyError(u"data", self._root, self.data._root)
                    if self.data._parent != self:
                        raise kaitaistruct.ConsistencyError(u"data", self, self.data._parent)
                else:
                    pass
                    if len(self.data) != self.len - 1:
                        raise kaitaistruct.ConsistencyError(u"data", self.len - 1, len(self.data))

            self._dirty = False


    class ManufacturerData(ReadWriteKaitaiStruct):
        """Govee manufacturer payload (valid only when is_govee)."""
        def __init__(self, _io=None, _parent=None, _root=None):
            super(GoveeAdvertisement.ManufacturerData, self).__init__(_io)
            self._parent = _parent
            self._root = _root

        def _read(self):
            self.flags = self._io.read_u1()
            self.company_id = self._io.read_u2le()
            self.pact_type = self._io.read_u2be()
            self.pact_code = self._io.read_u1()
            self.rest = self._io.read_bytes_full()
            self._dirty = False


        def _fetch_instances(self):
            pass


        def _write__seq(self, io=None):
            super(GoveeAdvertisement.ManufacturerData, self)._write__seq(io)
            self._io.write_u1(self.flags)
            self._io.write_u2le(self.company_id)
            self._io.write_u2be(self.pact_type)
            self._io.write_u1(self.pact_code)
            self._io.write_bytes(self.rest)
            if not self._io.is_eof():
                raise kaitaistruct.ConsistencyError(u"rest", 0, self._io.size() - self._io.pos())


        def _check(self):
            self._dirty = False

        @property
        def encrypted(self):
            if hasattr(self, '_m_encrypted'):
                return self._m_encrypted

            self._m_encrypted = self.flags & 64 != 0
            return getattr(self, '_m_encrypted', None)

        def _invalidate_encrypted(self):
            del self._m_encrypted
        @property
        def is_govee(self):
            if hasattr(self, '_m_is_govee'):
                return self._m_is_govee

            self._m_is_govee = self.company_id == 60552
            return getattr(self, '_m_is_govee', None)

        def _invalidate_is_govee(self):
            del self._m_is_govee
        @property
        def protocol_version(self):
            if hasattr(self, '_m_protocol_version'):
                return self._m_protocol_version

            self._m_protocol_version = self.flags & 15
            return getattr(self, '_m_protocol_version', None)

        def _invalidate_protocol_version(self):
            del self._m_protocol_version


