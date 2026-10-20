"""The spec's read-write classes, and the one place the library drives them.

Every frame, status stream, advertisement and upload value is read and written by the classes Kaitai
generates from ``spec/*.ksy`` (``tools/gen_kaitai.sh``). They are untyped, so they are ``Any`` here, and
this module is the library's one import of them. It follows the spec's read-write contract
(CLEAN_SPEC_DESIGN §1.7):

* reading: a parametric root takes its params and a stream, then ``_read()`` (:func:`read`,
  :func:`read_frame`); a field a case leaves out is an absent attribute (``getattr(x, name, None)``);
* writing: every node is linked to its parent (:func:`node`), checked bottom-up (``_check`` is shallow)
  and written into a zeroed buffer of the frame's length (:func:`serialize`); a frame's checksum, which
  the grammar can't compute, is written in a second pass by the frame's ``checksum_kind``
  (:func:`emit`).

The context a frame needs and doesn't carry, the device's ``govee_profile`` (command forms, counts,
colour-group rule, power rule, mode decode), is built by :func:`profile`, cached per set of facts so
every frame built for the same facts shares one profile object (parents check params by identity).
"""
from __future__ import annotations

import contextlib
import functools
import struct
from io import BytesIO
from typing import Any, Final

from kaitaistruct import (
    ConsistencyError,
    ConsistencyNotCheckedError,
    KaitaiStream,
    KaitaiStructError,
    ReadWriteKaitaiStruct,
)

from .._generated.govee_bgc_info import GoveeBgcInfo as _GoveeBgcInfo  # type: ignore[attr-defined]
from .._generated.govee_ble_frame import GoveeBleFrame as _GoveeBleFrame  # type: ignore[attr-defined]
from .._generated.govee_common import GoveeCommon as _GoveeCommon  # type: ignore[attr-defined]
from .._generated.govee_manufacturer_data import (  # type: ignore[attr-defined]
    GoveeManufacturerData as _GoveeManufacturerData,
)
from .._generated.govee_profile import GoveeProfile as _GoveeProfile  # type: ignore[attr-defined]
from .._generated.govee_scan_record import GoveeScanRecord as _GoveeScanRecord  # type: ignore[attr-defined]
from .._generated.govee_state import GoveeState as _GoveeState  # type: ignore[attr-defined]
from .._generated.govee_status_stream import (  # type: ignore[attr-defined]
    GoveeStatusStream as _GoveeStatusStream,
)
from .._generated.govee_upload import GoveeUpload as _GoveeUpload  # type: ignore[attr-defined]
from ..crypto import checksum
from ..exceptions import GoveeBleInvalidValue

# The spec's roots (one per .ksy) and the shared leaves and enums of govee_common.
Frame: Any = _GoveeBleFrame
Common: Any = _GoveeCommon
Profile: Any = _GoveeProfile
State: Any = _GoveeState
StatusStream: Any = _GoveeStatusStream
Upload: Any = _GoveeUpload
ManufacturerData: Any = _GoveeManufacturerData
ScanRecord: Any = _GoveeScanRecord
BgcInfo: Any = _GoveeBgcInfo

TX: Final = int(Common.Direction.tx)   # a frame the app writes (2b11)
RX: Final = int(Common.Direction.rx)   # a frame the device notifies (2b10)


def read(cls: Any, data: bytes, *params: Any) -> Any:
    """``data`` read as the generated type ``cls`` (``params`` are its ksy params, if any). Raises what
    the reader raises on input the type can't read."""
    obj = cls(*params, KaitaiStream(BytesIO(bytes(data))))
    obj._read()
    return obj


def read_frame(data: bytes, direction: int, prof: Any) -> Any:
    """``data`` read as a ``govee_ble_frame(direction, profile)``: ``pro_type``, the ``body`` its route
    picks (absent for a route with no case, which reads as raw bytes) and the ``checksum`` (none for
    0x05). The checksum is not verified here (the connection drops RX frames whose XOR fails before any
    read). Raises what the reader raises."""
    return read(Frame, data, direction, prof)


def try_read_frame(data: bytes, direction: int, prof: Any) -> Any:
    """:func:`read_frame`, or None when the frame doesn't read (too short, a body its case can't read)."""
    try:
        return read_frame(data, direction, prof)
    except Exception:  # noqa: BLE001 - a frame the spec can't read is not one the caller wants
        return None


def read_partial(cls: Any, data: bytes, *params: Any) -> tuple[Any, Exception | None]:
    """``data`` read as the generated type ``cls`` (``params`` its ksy params), and what stopped the read
    (None when it read whole). A read that stops part-way leaves set every field it got to, and a
    repeated field ends with the item it stopped in (the generated readers append it before the error
    propagates): where a stream stopped, and why, without reading any part of it on its own."""
    obj = cls(*params, KaitaiStream(BytesIO(bytes(data))))
    try:
        obj._read()
    except Exception as err:  # noqa: BLE001 - returned to the caller, which judges it
        return obj, err
    return obj, None


def frame_head(data: bytes, direction: int, prof: Any) -> Any:
    """The frame object after a read that may have stopped part-way: every field the reader got to is
    set (``pro_type``, ``body`` and the body's leading fields), the rest are absent. For matching a
    reply by its header when its body doesn't read."""
    f = Frame(direction, prof, KaitaiStream(BytesIO(bytes(data))))
    with contextlib.suppress(Exception):   # a body the spec can't read still has its header
        f._read()
    return f


# ── writing ──────────────────────────────────────────────────────────────────
def node(cls: Any, parent: Any, *params: Any, **fields: Any) -> Any:
    """A generated object to write: ``params`` are the type's ksy params, ``fields`` its values. Linked
    under ``parent`` (``_check`` verifies the links); a standalone object is its own root."""
    root = None if parent is None else parent._root
    obj = cls(*params, _io=None, _parent=parent, _root=root)
    if obj._root is None:
        obj._root = obj
    for name, value in fields.items():
        setattr(obj, name, value)
    return obj


def leaf(cls: Any, **fields: Any) -> Any:
    """An imported nested leaf of govee_common (``rgb``, ``bri_rgb``, ``echo``, ...): the generated code
    neither links nor checks it, so it takes no parent."""
    obj = cls()
    for name, value in fields.items():
        setattr(obj, name, value)
    return obj


def rgb(r: int, g: int, b: int) -> Any:
    """A ``govee_common::rgb``."""
    return leaf(Common.Rgb, r=r, g=g, b=b)


def echo() -> Any:
    """An empty ``govee_common::echo``: the rest of the body, zero-filled when written."""
    return leaf(Common.Echo, bytes=b"")


def seg_mask(parent: Any, num_segments: int, cap: int, mask: int) -> Any:
    """A ``govee_common::seg_mask(num_segments, cap)`` with the bits of ``mask`` (LSB first)."""
    m = node(Common.SegMask, parent, num_segments, cap)
    m.bits = [bool(mask >> i & 1) for i in range(8 * int(m.len_mask))]
    return m


def _check_tree(obj: Any) -> None:
    """``_check()`` every generated object under ``obj``, then ``obj`` itself: Kaitai checks one object
    at a time and refuses to write one that changed since its check."""
    for name, value in vars(obj).items():
        if name.startswith("_"):   # the links, the stream and the value-instance caches
            continue
        children = value if isinstance(value, list) else (value,)
        for child in children:
            if isinstance(child, ReadWriteKaitaiStruct) and child is not obj:
                _check_tree(child)
    obj._check()


def serialize(obj: Any, length: int) -> bytes:
    """``obj`` written into a zeroed ``length``-byte stream. A value the spec type can't carry (a
    ``valid`` it fails, a number too wide for its field, a body that doesn't fill its size) is
    GoveeBleInvalidValue."""
    try:
        _check_tree(obj)
        io = KaitaiStream(BytesIO(bytearray(length)))
        obj._write(io)
        return bytes(io.to_byte_array())
    except (KaitaiStructError, ConsistencyError, ConsistencyNotCheckedError, struct.error) as err:
        raise GoveeBleInvalidValue(f"value doesn't fit the spec's {type(obj).__name__}: {err}") from err


def _sum8(data: bytes) -> int:
    return sum(data) & 0xFF


def emit(frame_obj: Any, length: int) -> bytes:
    """A whole frame of ``length`` bytes. Its ``checksum`` (when its ``checksum_kind`` has one) is the one
    value computed here, over every byte before it: written once with 0, then with the XOR or the sum."""
    kind = frame_obj.checksum_kind
    if kind == Frame.ChecksumKind.none:
        return serialize(frame_obj, length)
    frame_obj.checksum = 0
    first = serialize(frame_obj, length)
    frame_obj.checksum = _sum8(first[:-1]) if kind == Frame.ChecksumKind.sum8 else checksum(first[:-1])
    return serialize(frame_obj, length)


def build_frame(pro_type: Any, prof: Any, make_body: Any, length: int) -> bytes:
    """A TX ``govee_ble_frame`` of ``pro_type`` under ``prof`` whose body ``make_body(frame)`` builds
    (linked to the frame), ``length`` bytes long."""
    f = node(Frame, None, TX, prof, pro_type=pro_type)
    f.body = make_body(f)
    return emit(f, length)


# ── the device profile (context params) ──────────────────────────────────────
@functools.lru_cache(maxsize=512)
def profile(
    *,
    f_01: int = 0, f_02: int = 0, f_09: int = 0, f_12: int = 0, f_13: int = 0, f_14: int = 0,
    f_30: int = 0, f_36: int = 0, f_40: int = 0, f_41: int = 0, f_a2: int = 0, f_a3: int = 0,
    f_a5: int = 0, f_b0: int = 0,
    f_0b: int = 0, f_15_01: int = 0, f_15_04: int = 0, f_15_05: int = 0, f_ee_30: int = 0,
    f_ee_40: int = 0,
    num_segments: int = 0, num_levels: int = 0,
    group_rule: int = 0, num_per_group: int = 0, has_brightness: bool = False, num_pieces: int = 0,
    relay_on_mask: int = 0, relay_heartbeat_on_mask: int = 0,
    c02: int = 0, c0b: int = 0, c0d: int = 0, c14: int = 0, c15: int = 0, music: int = 0,
    empty_fails: bool = False, mode_status: tuple[tuple[str, int], ...] | None = None,
) -> Any:
    """A ``govee_profile`` for one set of device facts, every one keyword-only and defaulting to the
    spec's ``unknown`` / 0. Cached: the same facts give the same object, which parents check params
    against by identity. The mode decode ``c02`` .. ``c15``, ``music`` and ``empty_fails`` is the aa 05
    reply's (``mode``); ``mode_status`` gives the status TLV 0x05's as ``(param, value)`` pairs where it
    differs (devices.yaml mode_color_parse_status, and a status row's wire mode: its music layout and
    empty-value rule): a colour sub-mode it leaves out is not decoded, a ``music`` / ``empty_fails`` it leaves
    out is the reply's. Without it the TLV takes the reply's decode. So a status TLV and an aa 05 reply of the
    same mode can decode differently."""
    p = Profile
    forms = p.CommandForms(
        f_01=f_01, f_02=f_02, f_09=f_09, f_12=f_12, f_13=f_13, f_14=f_14, f_30=f_30, f_36=f_36, f_40=f_40,
        f_41=f_41, f_a2=f_a2, f_a3=f_a3, f_a5=f_a5, f_b0=f_b0, f_0b=f_0b, f_15_01=f_15_01, f_15_04=f_15_04,
        f_15_05=f_15_05, f_ee_30=f_ee_30, f_ee_40=f_ee_40)
    decode = p.ModeDecode(c02=c02, c0b=c0b, c0d=c0d, c14=c14, c15=c15, music=music, empty_fails=empty_fails)
    status = decode if mode_status is None else p.ModeDecode(
        **{"c02": 0, "c0b": 0, "c0d": 0, "c14": 0, "c15": 0, "music": music, "empty_fails": empty_fails,
           **dict(mode_status)})
    return p(forms=forms, counts=p.DeviceCounts(num_segments=num_segments, num_levels=num_levels),
             group=p.ColorGroupForm(rule=group_rule, num_per_group=num_per_group,
                                    has_brightness=has_brightness, num_pieces=num_pieces),
             power=p.PowerRule(relay_on_mask=relay_on_mask, relay_heartbeat_on_mask=relay_heartbeat_on_mask),
             mode=decode, mode_status=status)


PLAIN: Final[Any] = profile()   # no device facts: every form unknown, every count 0
