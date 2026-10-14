"""Kelvin -> RGB "tint" table for the 0x0d colour-temperature write.

The app appends an RGB approximation of the colour temperature to the 0x0d CCT frame
(``Bytes.p`` / ``Bytes.A`` in base2light ``pact/newdetail/config/fuc/color/Bytes.java``,
bytes 6..8 = ``ColorUtils.m(Constant.getTemColorByKelvin(k)[2])``). ``getTemColorByKelvin``
(base2home ``Constant.java``) clamps to 1000..10000 K and returns the FIRST colour, in
insertion order, of the ``LinkedHashMap`` colour->Kelvin table whose value equals the
Kelvin exactly; any other Kelvin (not a multiple of 100) yields no tint (zeros). The
table below is that first-colour-per-Kelvin projection, generated from the decompiled
``Constant`` static initializer (Govee Home 7.7.02), with the duplicate-key ``put``
semantics of ``LinkedHashMap`` preserved.
"""
from __future__ import annotations

from typing import Final

# Kelvin (1000..10000, 100 K steps) -> 0xRRGGBB (Constant.getTemColorByKelvin, first match).
_TINT_BY_KELVIN: Final[dict[int, int]] = {
    1000: 0xFF3300, 1100: 0xFF4500, 1200: 0xFF5200, 1300: 0xFF5D00, 1400: 0xFF6600, 1500: 0xFF6F00,
    1600: 0xFF7600, 1700: 0xFF7C00, 1800: 0xFF8200, 1900: 0xFF8700, 2000: 0xFF8D0B, 2100: 0xFF921D,
    2200: 0xFF9829, 2300: 0xFF9D33, 2400: 0xFFA23C, 2500: 0xFFA645, 2600: 0xFFAA4D, 2700: 0xFFAE54,
    2800: 0xFFB25B, 2900: 0xFFB662, 3000: 0xFFB969, 3100: 0xFFBD6F, 3200: 0xFFC076, 3300: 0xFFC37C,
    3400: 0xFFC682, 3500: 0xFFC987, 3600: 0xFFCB8D, 3700: 0xFFCE92, 3800: 0xFFD097, 3900: 0xFFD39C,
    4000: 0xFFD5A1, 4100: 0xFFD7A6, 4200: 0xFFD9AB, 4300: 0xFFDBAF, 4400: 0xFFDDB4, 4500: 0xFFDFB8,
    4600: 0xFFE1BC, 4700: 0xFFE2C0, 4800: 0xFFE4C4, 4900: 0xFFE5C8, 5000: 0xFFE7CC, 5100: 0xFFE8D0,
    5200: 0xFFEAD3, 5300: 0xFFEBD7, 5400: 0xFFEDDA, 5500: 0xFFEEDE, 5600: 0xFFEFE1, 5700: 0xFFF0E4,
    5800: 0xFFF1E7, 5900: 0xFFF3EA, 6000: 0xFFF4ED, 6100: 0xFFF5F0, 6200: 0xFFF6F3, 6300: 0xFFF7F7,
    6400: 0xFFF8F8, 6500: 0xFFF9FB, 6600: 0xFFF9FD, 6700: 0xFEFAFF, 6800: 0xFCF8FF, 6900: 0xFAF7FF,
    7000: 0xF7F5FF, 7100: 0xF5F4FF, 7200: 0xF3F3FF, 7300: 0xF1F1FF, 7400: 0xEFF0FF, 7500: 0xEEEFFF,
    7600: 0xECEEFF, 7700: 0xEAEDFF, 7800: 0xE9ECFF, 7900: 0xE7EAFF, 8000: 0xE5E9FF, 8100: 0xE4E9FF,
    8200: 0xE3E8FF, 8300: 0xE1E7FF, 8400: 0xE0E6FF, 8500: 0xDFE5FF, 8600: 0xDDE4FF, 8700: 0xDCE3FF,
    8800: 0xDBE2FF, 8900: 0xDAE2FF, 9000: 0xD9E1FF, 9100: 0xD8E0FF, 9200: 0xD7DFFF, 9300: 0xD6DFFF,
    9400: 0xD5DEFF, 9500: 0xD4DDFF, 9600: 0xD3DDFF, 9700: 0xD2DCFF, 9800: 0xD1DCFF, 9900: 0xD0DBFF,
    10000: 0xCFDAFF,
}


def tint_for_kelvin(kelvin: int) -> tuple[int, int, int]:
    """RGB tint bytes the app sends after the Kelvin in a 0x0d CCT frame.

    Port of ``Constant.getTemColorByKelvin(kelvin)``: outside 1000..10000 K, or for a
    Kelvin with no exact table entry, the app leaves the three bytes zero."""
    rgb = _TINT_BY_KELVIN.get(kelvin)
    if rgb is None:
        return (0, 0, 0)
    return (rgb >> 16) & 0xFF, (rgb >> 8) & 0xFF, rgb & 0xFF
