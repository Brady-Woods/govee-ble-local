"""Percent <-> raw brightness for devices whose brightness byte is not a percent.

The app keeps a per-device raw range (BrightnessConfig.Config: version 0 = [20, 254],
2 = [6, 254], 3 = [40, 254], 1 = percent [1, 100]) and converts the 1..100 slider with
``NumberUtil.calculateProgress`` (write) / ``NumberUtil.calculatePercent`` (read). Both use
Java ``float`` arithmetic, emulated here so the bytes match the app exactly.
"""
from __future__ import annotations

import math
import struct


def _f32(x: float) -> float:
    """Round to IEEE-754 single precision (Java ``float``)."""
    return float(struct.unpack("<f", struct.pack("<f", x))[0])


def _step(lo: int, hi: int) -> float:
    return _f32(_f32((hi - lo) + 3) / _f32(100.0))   # ((max - min) + 3) / 100.0f


def percent_to_raw(pct: int, lo: int, hi: int) -> int:
    """``NumberUtil.calculateProgress(max, min, percent)``:
    ``(int) (min + (percent - 1) * (((max - min) + 3) / 100.0f))``, for pct in 1..100."""
    pct = max(1, min(100, pct))
    return int(_f32(lo + _f32((pct - 1) * _step(lo, hi))))


def percent_to_device(pct: int, lo: int, hi: int) -> int:
    """The brightness byte the app writes for ``pct`` on a device whose raw range is ``[lo, hi]``
    (``BrightnessConfig.Companion.h`` -> ``c``): ``pct`` clamped to 1..100, written as it is when the
    range is the percent range [1, 100] (version 1), else :func:`percent_to_raw`."""
    if lo == 1 and hi == 100:
        return max(1, min(100, pct))
    return percent_to_raw(pct, lo, hi)


def raw_to_percent(raw: int, lo: int, hi: int) -> int:
    """``NumberUtil.calculatePercent(max, min, progress)``:
    ``(int) (Math.ceil((progress - min) / (((max - min) + 3) / 100.0f)) + 1.0)``, clamped
    to 0..100 (the app does not clamp; a reply below ``min`` would go negative)."""
    pct = int(math.ceil(_f32((raw - lo) / _step(lo, hi))) + 1.0)
    return max(0, min(100, pct))
