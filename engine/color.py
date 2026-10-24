"""Colour mathematics: sRGB bytes -> linear light -> luminance, chromaticity, redness.

Every function here is element-wise and works in float64, so the same code
serves one pixel, a whole frame or a grid of cells.  The formulas are the ones
WCAG 2.2 prints (docs/SPEC.md 3.4 and 3.6).  The only additions are the reverse
transfer function (needed to re-encode a cell's mean colour, ADR-003) and the
black special case for chromaticity (ADR-011).

Why "linear" matters: the bytes in a screenshot are gamma-encoded, so byte 128
is not half as bright as byte 255.  WCAG's thresholds are about physical light,
so every pixel is first converted to linear light and all averaging and
thresholding happens there.
"""

from __future__ import annotations

import numpy as np

from engine.constants import (
    D65_UV,
    LEGACY_RED_VALUE_SCALE,
    LUMINANCE_COEFFICIENTS,
    SRGB_ENCODE_CUTOFF,
    SRGB_GAMMA,
    SRGB_GAMMA_OFFSET,
    SRGB_GAMMA_SCALE,
    SRGB_LINEAR_CUTOFF,
    SRGB_LINEAR_DIVISOR,
    SRGB_TO_XYZ,
)


def _linearise_encoded(encoded: np.ndarray) -> np.ndarray:
    """Apply WCAG's per-channel formula to sRGB values already scaled to 0..1."""
    # WCAG 2.2 glossary: "if RsRGB <= 0.04045 then R = RsRGB/12.92"
    small = encoded / SRGB_LINEAR_DIVISOR
    # WCAG 2.2 glossary: "else R = ((RsRGB+0.055)/1.055) ^ 2.4"
    large = ((encoded + SRGB_GAMMA_OFFSET) / SRGB_GAMMA_SCALE) ** SRGB_GAMMA
    return np.where(encoded <= SRGB_LINEAR_CUTOFF, small, large)


def _build_lookup_table() -> np.ndarray:
    """Linear-light value for each of the 256 possible byte values.

    A frame has millions of bytes but only 256 distinct values, so computing
    the formula once per value and looking the answers up is both faster and
    guarantees every pixel with the same byte gets the exact same float.
    """
    # WCAG 2.2 glossary: "RsRGB = R8bit/255"
    encoded = np.arange(256, dtype=np.float64) / 255.0
    table = _linearise_encoded(encoded)
    table.setflags(write=False)  # a shared table must never be edited in place
    return table


SRGB8_TO_LINEAR: np.ndarray = _build_lookup_table()


def srgb8_to_linear(frame_u8: np.ndarray) -> np.ndarray:
    """uint8 sRGB values (any shape) -> linear light float64 of the same shape."""
    frame = np.asarray(frame_u8)
    if frame.dtype != np.uint8:
        raise ValueError(f"expected uint8 sRGB values, got dtype {frame.dtype}")
    return SRGB8_TO_LINEAR[frame]


def linear_to_srgb(linear: np.ndarray) -> np.ndarray:
    """Linear light 0..1 -> sRGB-encoded 0..1 (the reverse of the WCAG formula).

    Not rounded to bytes: the redness ratio R/(R+G+B) is scale-free, so the
    unrounded 0..1 values give the same ratio as 0..255 values would, without
    adding rounding noise to a cell's mean colour.
    """
    lin = np.maximum(np.asarray(linear, dtype=np.float64), 0.0)
    # Reverse of WCAG's "R = RsRGB/12.92" branch (IEC 61966-2-1): C = 12.92 * L
    small = lin * SRGB_LINEAR_DIVISOR
    # Reverse of WCAG's "((RsRGB+0.055)/1.055) ^ 2.4": C = 1.055 * L^(1/2.4) - 0.055
    large = SRGB_GAMMA_SCALE * lin ** (1.0 / SRGB_GAMMA) - SRGB_GAMMA_OFFSET
    return np.where(lin <= SRGB_ENCODE_CUTOFF, small, large)


def relative_luminance(linear_rgb: np.ndarray) -> np.ndarray:
    """Relative luminance L of linear RGB [..., 3] -> [...] in 0..1.

    Written as three explicit multiply-adds (not a matrix product) so the
    floating-point operation order is fixed and the result is bit-identical on
    every run and machine.
    """
    rgb = np.asarray(linear_rgb, dtype=np.float64)
    kr, kg, kb = LUMINANCE_COEFFICIENTS
    # WCAG 2.2 glossary: "L = 0.2126 * R + 0.7152 * G + 0.0722 * B"
    return kr * rgb[..., 0] + kg * rgb[..., 1] + kb * rgb[..., 2]


def xyz_from_linear(linear_rgb: np.ndarray) -> np.ndarray:
    """Linear RGB [..., 3] -> CIE XYZ [..., 3] with the sRGB / D65 matrix."""
    rgb = np.asarray(linear_rgb, dtype=np.float64)
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    (xr, xg, xb), (yr, yg, yb), (zr, zg, zb) = SRGB_TO_XYZ
    # Understanding 2.3.1: "X = 0.4124564 * R + 0.3575761 * G + 0.1804375 * B" (4-dp form)
    x = xr * r + xg * g + xb * b
    # Understanding 2.3.1: "Y = 0.2126729 * R + 0.7151522 * G + 0.0721750 * B" (== L row)
    y = yr * r + yg * g + yb * b
    # Understanding 2.3.1: "Z = 0.0193339 * R + 0.1191920 * G + 0.9503041 * B" (4-dp form)
    z = zr * r + zg * g + zb * b
    return np.stack([x, y, z], axis=-1)


def uv_chromaticity(linear_rgb: np.ndarray) -> np.ndarray:
    """Linear RGB [..., 3] -> CIE 1976 UCS (u', v') [..., 2].

    Chromaticity is "which colour, ignoring brightness".  Pure black has no
    brightness at all, so the formula divides 0 by 0; FlashTrace gives black
    the D65 white point so it counts as colourless, like white and grey
    (ADR-011).  Red <-> black then measures as a chromaticity change of 0.259,
    which is why a red strobe on black is still a red flash under WCAG 2.2.
    """
    xyz = xyz_from_linear(linear_rgb)
    x, y, z = xyz[..., 0], xyz[..., 1], xyz[..., 2]
    # Understanding 2.3.1: "u' = 4 * X / (X + 15 * Y + 3 * Z)"
    #                      "v' = 9 * Y / (X + 15 * Y + 3 * Z)"
    denominator = x + 15.0 * y + 3.0 * z
    defined = denominator != 0.0
    u = np.full(denominator.shape, D65_UV[0], dtype=np.float64)
    v = np.full(denominator.shape, D65_UV[1], dtype=np.float64)
    np.divide(4.0 * x, denominator, out=u, where=defined)
    np.divide(9.0 * y, denominator, out=v, where=defined)
    return np.stack([u, v], axis=-1)


def redness_ratio(linear_rgb: np.ndarray) -> np.ndarray:
    """Linear RGB [..., 3] -> R / (R + G + B) on sRGB-ENCODED components [...].

    WCAG does not say which scale R, G, B are on; the build brief fixes the
    0..255 encoded scale and ADR-003 records it.  The ratio is scale-free, so
    the 0..1 encoded values used here give exactly the 0..255 answer.  Black
    (sum 0) gets ratio 0: it is not red.
    """
    encoded = linear_to_srgb(linear_rgb)
    r, g, b = encoded[..., 0], encoded[..., 1], encoded[..., 2]
    total = r + g + b
    ratio = np.zeros(total.shape, dtype=np.float64)
    # WCAG 2.2 glossary Note 3: "a value R/(R + G + B)"
    np.divide(r, total, out=ratio, where=total != 0.0)
    return ratio


def legacy_red_value(linear_rgb: np.ndarray) -> np.ndarray:
    """Linear RGB [..., 3] -> pre-2022 red value max(0, R - G - B) x 320 [...].

    Computed on sRGB-encoded 0..1 components, as the build brief specifies.
    Only the "wcag21-legacy" comparison mode uses it (ADR-001).
    """
    encoded = linear_to_srgb(linear_rgb)
    r, g, b = encoded[..., 0], encoded[..., 1], encoded[..., 2]
    # Build brief / WCAG 2.0-2.1 era working value: "(R - G - B) x 320", floored at 0
    return np.maximum(r - g - b, 0.0) * LEGACY_RED_VALUE_SCALE
