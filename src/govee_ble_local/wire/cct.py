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


# 0xRRGGBB -> Kelvin for EVERY colour of that table: the app's two colours per Kelvin step, 178 in all
# (``Constant.k2`` and its identical copy ``ColorUtils.e``). ``tint_for_kelvin`` above takes the first
# colour of each Kelvin; this is the reverse lookup the app uses to decide that a picked colour IS a
# colour temperature (``KelvinConfig.i`` / ``ColorUtils.i``, ``Constant.getColorTemKelvin``).
_KELVIN_BY_TINT: Final[dict[int, int]] = {
    0xFF3300: 1000, 0xFF3800: 1000, 0xFF4500: 1100, 0xFF4700: 1100,
    0xFF5200: 1200, 0xFF5300: 1200, 0xFF5D00: 1300, 0xFF6600: 1400,
    0xFF6500: 1400, 0xFF6F00: 1500, 0xFF6D00: 1500, 0xFF7600: 1600,
    0xFF7300: 1600, 0xFF7C00: 1700, 0xFF7900: 1700, 0xFF8200: 1800,
    0xFF7E00: 1800, 0xFF8700: 1900, 0xFF8300: 1900, 0xFF8D0B: 2000,
    0xFF8912: 2000, 0xFF921D: 2100, 0xFF8E21: 2100, 0xFF9829: 2200,
    0xFF932C: 2200, 0xFF9D33: 2300, 0xFF9836: 2300, 0xFFA23C: 2400,
    0xFF9D3F: 2400, 0xFFA645: 2500, 0xFFA148: 2500, 0xFFAA4D: 2600,
    0xFFA54F: 2600, 0xFFAE54: 2700, 0xFFA957: 2700, 0xFFB25B: 2800,
    0xFFAD5E: 2800, 0xFFB662: 2900, 0xFFB165: 2900, 0xFFB969: 3000,
    0xFFB46B: 3000, 0xFFBD6F: 3100, 0xFFB872: 3100, 0xFFC076: 3200,
    0xFFBB78: 3200, 0xFFC37C: 3300, 0xFFBE7E: 3300, 0xFFC682: 3400,
    0xFFC184: 3400, 0xFFC987: 3500, 0xFFC489: 3500, 0xFFCB8D: 3600,
    0xFFC78F: 3600, 0xFFCE92: 3700, 0xFFC994: 3700, 0xFFD097: 3800,
    0xFFCC99: 3800, 0xFFD39C: 3900, 0xFFCE9F: 3900, 0xFFD5A1: 4000,
    0xFFD1A3: 4000, 0xFFD7A6: 4100, 0xFFD3A8: 4100, 0xFFD9AB: 4200,
    0xFFD5AD: 4200, 0xFFDBAF: 4300, 0xFFD7B1: 4300, 0xFFDDB4: 4400,
    0xFFD9B6: 4400, 0xFFDFB8: 4500, 0xFFDBBA: 4500, 0xFFE1BC: 4600,
    0xFFDDBE: 4600, 0xFFE2C0: 4700, 0xFFDFC2: 4700, 0xFFE4C4: 4800,
    0xFFE1C6: 4800, 0xFFE5C8: 4900, 0xFFE3CA: 4900, 0xFFE7CC: 5000,
    0xFFE4CE: 5000, 0xFFE8D0: 5100, 0xFFE6D2: 5100, 0xFFEAD3: 5200,
    0xFFE8D5: 5200, 0xFFEBD7: 5300, 0xFFE9D9: 5300, 0xFFEDDA: 5400,
    0xFFEBDC: 5400, 0xFFEEDE: 5500, 0xFFECE0: 5500, 0xFFEFE1: 5600,
    0xFFEEE3: 5600, 0xFFF0E4: 5700, 0xFFEFE6: 5700, 0xFFF1E7: 5800,
    0xFFF0E9: 5800, 0xFFF3EA: 5900, 0xFFF2EC: 5900, 0xFFF4ED: 6000,
    0xFFF3EF: 6000, 0xFFF5F0: 6100, 0xFFF4F2: 6100, 0xFFF6F3: 6200,
    0xFFF5F5: 6200, 0xFFF7F7: 6300, 0xFFF6F8: 6300, 0xFFF8F8: 6400,
    0xFFF8FB: 6400, 0xFFF9FB: 6500, 0xFFF9FD: 6600, 0xFEF9FF: 6600,
    0xFEFAFF: 6700, 0xFCF7FF: 6700, 0xFCF8FF: 6800, 0xF9F6FF: 6800,
    0xFAF7FF: 6900, 0xF7F5FF: 7000, 0xF5F3FF: 7000, 0xF5F4FF: 7100,
    0xF3F2FF: 7100, 0xF3F3FF: 7200, 0xF0F1FF: 7200, 0xF1F1FF: 7300,
    0xEFF0FF: 7400, 0xEDEFFF: 7400, 0xEEEFFF: 7500, 0xEBEEFF: 7500,
    0xECEEFF: 7600, 0xE9EDFF: 7600, 0xEAEDFF: 7700, 0xE7ECFF: 7700,
    0xE9ECFF: 7800, 0xE6EBFF: 7800, 0xE7EAFF: 7900, 0xE4EAFF: 7900,
    0xE5E9FF: 8000, 0xE3E9FF: 8000, 0xE4E9FF: 8100, 0xE1E8FF: 8100,
    0xE3E8FF: 8200, 0xE0E7FF: 8200, 0xE1E7FF: 8300, 0xDEE6FF: 8300,
    0xE0E6FF: 8400, 0xDDE6FF: 8400, 0xDFE5FF: 8500, 0xDCE5FF: 8500,
    0xDDE4FF: 8600, 0xDAE4FF: 8600, 0xDCE3FF: 8700, 0xD9E3FF: 8700,
    0xDBE2FF: 8800, 0xD8E3FF: 8800, 0xDAE2FF: 8900, 0xD7E2FF: 8900,
    0xD9E1FF: 9000, 0xD6E1FF: 9000, 0xD8E0FF: 9100, 0xD4E1FF: 9100,
    0xD7DFFF: 9200, 0xD3E0FF: 9200, 0xD6DFFF: 9300, 0xD2DFFF: 9300,
    0xD5DEFF: 9400, 0xD1DFFF: 9400, 0xD4DDFF: 9500, 0xD0DEFF: 9500,
    0xD3DDFF: 9600, 0xCFDFFF: 9600, 0xD2DCFF: 9700, 0xCFDCFF: 9700,
    0xD1DCFF: 9800, 0xCEDCFF: 9800, 0xD0DBFF: 9900, 0xCDCDFF: 9900,
    0xCFDAFF: 10000, 0xCCDBFF: 10000,
}


def kelvin_for_tint(rgb: tuple[int, int, int]) -> int | None:
    """Kelvin of ``rgb`` if it is exactly one of the app's colour-temperature tint colours, else None.

    Port of the table lookup behind ``KelvinConfig.i(colour, info)`` and ``Constant.getColorTemKelvin``
    (the app compares the full ARGB int, alpha 0xFF). Both colours of a Kelvin step map to that Kelvin;
    the caller applies the device's Kelvin range (``KelvinConfig.i`` keeps only colours whose Kelvin
    lies in it)."""
    return _KELVIN_BY_TINT.get((rgb[0] << 16) | (rgb[1] << 8) | rgb[2])
