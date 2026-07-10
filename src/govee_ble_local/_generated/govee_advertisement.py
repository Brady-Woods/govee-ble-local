# This is a generated file! Please edit source .ksy file and use kaitai-struct-compiler to rebuild
# type: ignore

import kaitaistruct
from kaitaistruct import KaitaiStruct, KaitaiStream, BytesIO


if getattr(kaitaistruct, 'API_VERSION', (0, 9)) < (0, 11):
    raise Exception("Incompatible Kaitai Struct Python API: 0.11 or later is required, but you have %s" % (kaitaistruct.__version__))

class GoveeAdvertisement(KaitaiStruct):
    """Connectionless parse of a BLE scan record. The app walks AD structures `[len][type][data]` and
    reads the Govee identity/state from the manufacturer-specific AD (type 0xFF). See
    GOVEE_BLE_GATT_PROTOCOL.md §19 and base2home/pact/BleUtil.java:829 (parseBleBroadcastPact).
    
    NOTE: Govee's manufacturer-data layout is custom — a `flags` byte PRECEDES the `88 EC` marker
    (i.e. it is not a standard leading 2-byte company identifier). `manufacturer_data` therefore only
    makes sense when `is_govee` is true; for other vendors' 0xFF ADs the fields are meaningless.
    Termination: a `len == 0` structure marks the end of meaningful data (zero padding).
    """
    def __init__(self, _io, _parent=None, _root=None):
        super(GoveeAdvertisement, self).__init__(_io)
        self._parent = _parent
        self._root = _root or self
        self._read()

    def _read(self):
        self.structures = []
        i = 0
        while True:
            _ = GoveeAdvertisement.AdStructure(self._io, self, self._root)
            self.structures.append(_)
            if _.len == 0:
                break
            i += 1


    def _fetch_instances(self):
        pass
        for i in range(len(self.structures)):
            pass
            self.structures[i]._fetch_instances()


    class AdStructure(KaitaiStruct):
        def __init__(self, _io, _parent=None, _root=None):
            super(GoveeAdvertisement.AdStructure, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

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
                else:
                    pass
                    self.data = self._io.read_bytes(self.len - 1)



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



    class ManufacturerData(KaitaiStruct):
        """Govee manufacturer payload (valid only when is_govee)."""
        def __init__(self, _io, _parent=None, _root=None):
            super(GoveeAdvertisement.ManufacturerData, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.flags = self._io.read_u1()
            self.company_id = self._io.read_u2le()
            self.pact_type = self._io.read_u2be()
            self.pact_code = self._io.read_u1()
            self.rest = self._io.read_bytes_full()


        def _fetch_instances(self):
            pass

        @property
        def encrypted(self):
            if hasattr(self, '_m_encrypted'):
                return self._m_encrypted

            self._m_encrypted = self.flags & 64 != 0
            return getattr(self, '_m_encrypted', None)

        @property
        def is_govee(self):
            if hasattr(self, '_m_is_govee'):
                return self._m_is_govee

            self._m_is_govee = self.company_id == 60552
            return getattr(self, '_m_is_govee', None)

        @property
        def protocol_version(self):
            if hasattr(self, '_m_protocol_version'):
                return self._m_protocol_version

            self._m_protocol_version = self.flags & 15
            return getattr(self, '_m_protocol_version', None)



