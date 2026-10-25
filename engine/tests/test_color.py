"""Black-box unit tests for engine/color.py (SPEC T1, SPEC 5.3, ADR-003, ADR-011, ADR-020).

Every expected number here was derived by hand or brute force from the W3C
formulas quoted verbatim in docs/SPEC.md sections 3.4 and 3.6; nothing was read
from the implementation.  The tests use float64 throughout and compare with
explicit tolerances so a reader can see exactly how tight each pin is.
"""
from __future__ import annotations

import math

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from engine.color import (
    SRGB8_TO_LINEAR,
    legacy_red_value,
    linear_to_srgb,
    redness_ratio,
    relative_luminance,
    srgb8_from_linear,
    srgb8_to_linear,
    uv_chromaticity,
    xyz_from_linear,
)
from engine.constants import (
    D65_UV,
    LEGACY_RED_MIN_DELTA,
    LEGACY_RED_VALUE_SCALE,
    RED_MIN_R8,
    RED_SATURATION_MIN_RATIO,
)

# Hypothesis settings: small, deterministic, no wall-clock deadline (SPEC 5: no randomness).
_PROPERTY = settings(deadline=None, derandomize=True, max_examples=40, database=None)

# sRGB red in CIE 1976 UCS from the SPEC 5.3 matrix: X = 0.4124, Y = 0.2126, Z = 0.0193, so
# u' = 4X / (X + 15Y + 3Z) = 1.6496 / 3.6593 = 0.45080 and v' = 9Y / 3.6593 = 0.52289.
# Written to 4 decimals, so the tests compare with a tolerance of 5e-5.
_RED_UV = (0.4508, 0.5229)
_UV_TOL = 5e-5


def _rgb8(r: int, g: int, b: int) -> np.ndarray:
    """One pixel as a uint8 array of shape (3,)."""
    return np.array([r, g, b], dtype=np.uint8)


def _lin(r: int, g: int, b: int) -> np.ndarray:
    """Linear RGB of one sRGB8 pixel, obtained through the module under test."""
    return srgb8_to_linear(_rgb8(r, g, b))


def _wcag_linear(c8: int) -> float:
    """Reference transfer function typed straight from WCAG 2.2 'relative luminance' Note 1."""
    c = c8 / 255
    if c <= 0.04045:
        return c / 12.92
    return ((c + 0.055) / 1.055) ** 2.4


# ---------------------------------------------------------------------------
# SRGB8_TO_LINEAR lookup table
# ---------------------------------------------------------------------------


def test_lut_shape_and_dtype() -> None:
    """SPEC T1: one float64 linear value per 8-bit sRGB code (256 entries)."""
    assert SRGB8_TO_LINEAR.shape == (256,)
    assert SRGB8_TO_LINEAR.dtype == np.float64


def test_lut_endpoints_are_exact() -> None:
    """SPEC 3.4: luminance is 'normalized to 0 for darkest black and 1 for lightest white'."""
    assert SRGB8_TO_LINEAR[0] == 0.0
    assert SRGB8_TO_LINEAR[255] == 1.0


def test_lut_is_strictly_monotone() -> None:
    """SPEC T1: the sRGB transfer function is strictly increasing, so the LUT must be too."""
    assert np.all(np.diff(SRGB8_TO_LINEAR) > 0)


def test_lut_linear_branch_at_code_10() -> None:
    """SPEC T1: 10/255 = 0.0392 <= 0.04045, so code 10 uses the C/12.92 branch."""
    assert SRGB8_TO_LINEAR[10] == pytest.approx(10 / 255 / 12.92, abs=1e-15)


def test_lut_power_branch_at_code_11() -> None:
    """SPEC T1: 11/255 = 0.0431 > 0.04045, so code 11 uses the ((C+0.055)/1.055)^2.4 branch."""
    expected = ((11 / 255 + 0.055) / 1.055) ** 2.4
    assert SRGB8_TO_LINEAR[11] == pytest.approx(expected, abs=1e-15)
    # The linear branch would give a visibly different number; this pins the cutoff position.
    assert abs(SRGB8_TO_LINEAR[11] - 11 / 255 / 12.92) > 1e-6


def test_lut_matches_reference_formula_for_every_code() -> None:
    """SPEC T1: the whole table equals the WCAG formula evaluated code by code."""
    expected = np.array([_wcag_linear(i) for i in range(256)])
    np.testing.assert_allclose(SRGB8_TO_LINEAR, expected, rtol=0, atol=1e-15)


# ---------------------------------------------------------------------------
# srgb8_to_linear / linear_to_srgb
# ---------------------------------------------------------------------------


def test_srgb8_to_linear_preserves_shape_and_returns_float64() -> None:
    """SPEC 5.2: frames are linearised per channel, pixel by pixel, keeping the array shape."""
    frame = np.array(
        [[[0, 128, 255], [10, 11, 12], [200, 100, 50]],
         [[255, 255, 255], [0, 0, 0], [64, 64, 64]]],
        dtype=np.uint8,
    )
    out = srgb8_to_linear(frame)
    assert out.shape == frame.shape
    assert out.dtype == np.float64
    np.testing.assert_array_equal(out, SRGB8_TO_LINEAR[frame])


def test_srgb8_to_linear_white_and_black() -> None:
    """SPEC 3.4: white linearises to (1, 1, 1) and black to (0, 0, 0) exactly."""
    np.testing.assert_array_equal(_lin(255, 255, 255), np.array([1.0, 1.0, 1.0]))
    np.testing.assert_array_equal(_lin(0, 0, 0), np.array([0.0, 0.0, 0.0]))


def test_linear_to_srgb_inverts_the_lut_for_all_256_codes() -> None:
    """SPEC 5.3: the cell colour is re-encoded to sRGB; encoding must undo linearisation."""
    encoded = linear_to_srgb(SRGB8_TO_LINEAR)
    assert encoded.shape == (256,)
    assert encoded.dtype == np.float64
    np.testing.assert_allclose(encoded, np.arange(256) / 255, rtol=0, atol=1e-9)


def test_linear_to_srgb_is_not_rounded_to_8_bit() -> None:
    """linear_to_srgb is the plain inverse formula; the rounding to 0-255 lives in srgb8_from_linear."""
    value = linear_to_srgb(np.array([0.5]))[0]
    assert value == pytest.approx(1.055 * 0.5 ** (1 / 2.4) - 0.055, abs=1e-12)
    # 0.7354 is not on the k/255 grid, so a rounded implementation would fail here.
    assert abs(value * 255 - round(value * 255)) > 1e-3


def test_srgb8_from_linear_gives_back_every_byte_exactly() -> None:
    """SPEC 5.3 / ADR-020: re-encoding a uniform cell gives back its whole 0-255 value, for all 256 codes."""
    np.testing.assert_array_equal(srgb8_from_linear(SRGB8_TO_LINEAR), np.arange(256, dtype=np.float64))


def test_srgb8_from_linear_rounds_halves_up() -> None:
    """SPEC 5.3: whole values are floor(x + 0.5), so 2.5 -> 3 and 4.5 -> 5 (Python's round() gives 2 and 4)."""
    halves = np.array([0.5, 2.5, 4.5])
    # In the linear branch (x 12.92) these encode back to exactly k + 0.5; checked first so the test proves
    # the rounding direction and not a floating-point accident.
    linear = halves / 255.0 / 12.92
    assert (linear_to_srgb(linear) * 255.0).tolist() == halves.tolist()
    cells = np.repeat(linear[:, None], 3, axis=1)
    assert srgb8_from_linear(cells)[:, 0].tolist() == [1.0, 3.0, 5.0]


def test_linear_to_srgb_cutoff_branches() -> None:
    """SPEC T1 (inverse): below 0.0031308 the encoder is linear (x12.92), above it is the power law."""
    low, high = linear_to_srgb(np.array([0.003, 0.0032]))
    assert low == pytest.approx(0.003 * 12.92, abs=1e-15)
    assert high == pytest.approx(1.055 * 0.0032 ** (1 / 2.4) - 0.055, abs=1e-15)


def test_linear_to_srgb_endpoints() -> None:
    """SPEC 3.4: 0 and 1 are fixed points of the sRGB encoder."""
    out = linear_to_srgb(np.array([0.0, 1.0]))
    assert out[0] == pytest.approx(0.0, abs=1e-15)
    assert out[1] == pytest.approx(1.0, abs=1e-12)


# ---------------------------------------------------------------------------
# relative_luminance
# ---------------------------------------------------------------------------


def test_luminance_of_white_is_exactly_one() -> None:
    """SPEC T1: 0.2126 + 0.7152 + 0.0722 = 1.0, so white has L == 1.0 exactly."""
    assert relative_luminance(_lin(255, 255, 255)) == 1.0


def test_luminance_of_black_is_exactly_zero() -> None:
    """SPEC T1: all channels linearise to 0, so L == 0.0 exactly."""
    assert relative_luminance(_lin(0, 0, 0)) == 0.0


def test_luminance_of_mid_grey() -> None:
    """SPEC T1: L(128,128,128) = ((128/255+0.055)/1.055)^2.4 = 0.21586 (hand computed)."""
    assert relative_luminance(_lin(128, 128, 128)) == pytest.approx(0.21586, abs=1e-4)


def test_luminance_of_primaries_are_the_coefficients() -> None:
    """SPEC T1: pure red/green/blue linearise to 1 on one channel, so L is the coefficient itself."""
    assert relative_luminance(_lin(255, 0, 0)) == pytest.approx(0.2126, abs=1e-12)
    assert relative_luminance(_lin(0, 255, 0)) == pytest.approx(0.7152, abs=1e-12)
    assert relative_luminance(_lin(0, 0, 255)) == pytest.approx(0.0722, abs=1e-12)


def test_luminance_drops_the_channel_axis() -> None:
    """SPEC 5.3: L[t, y, x] is one number per cell, so [..., 3] -> [...]."""
    rgb = np.zeros((4, 5, 3), dtype=np.float64)
    out = relative_luminance(rgb)
    assert out.shape == (4, 5)
    assert out.dtype == np.float64


@_PROPERTY
@given(st.integers(min_value=0, max_value=255))
def test_grey_luminance_equals_lut_entry(code: int) -> None:
    """SPEC T1: the coefficients sum to 1, so any grey's L equals its single linear value."""
    assert relative_luminance(_lin(code, code, code)) == pytest.approx(
        SRGB8_TO_LINEAR[code], abs=1e-12
    )


@_PROPERTY
@given(st.lists(st.floats(0.0, 1.0), min_size=3, max_size=3))
def test_luminance_stays_inside_unit_interval(rgb: list[float]) -> None:
    """SPEC 3.4: relative luminance is normalised to the range 0..1."""
    lum = float(relative_luminance(np.array(rgb, dtype=np.float64)))
    assert 0.0 <= lum <= 1.0 + 1e-12


# ---------------------------------------------------------------------------
# xyz_from_linear
# ---------------------------------------------------------------------------


def test_xyz_of_white_is_d65() -> None:
    """SPEC 5.3: the sRGB/D65 matrix rows sum to (0.9505, 1.0, 1.089) for white."""
    xyz = xyz_from_linear(_lin(255, 255, 255))
    assert xyz.shape == (3,)
    np.testing.assert_allclose(xyz, [0.9505, 1.0, 1.089], rtol=0, atol=1e-9)


def test_xyz_of_red_is_first_matrix_column() -> None:
    """SPEC 5.3: X = 0.4124 R, Y = 0.2126 R, Z = 0.0193 R for pure red."""
    np.testing.assert_allclose(
        xyz_from_linear(_lin(255, 0, 0)), [0.4124, 0.2126, 0.0193], rtol=0, atol=1e-12
    )


def test_xyz_y_row_equals_relative_luminance() -> None:
    """SPEC 5.3: 'the Y row is identical to T1', so Y == L for any colour."""
    rng = np.random.default_rng(7)
    rgb = rng.random((6, 4, 3))
    np.testing.assert_allclose(
        xyz_from_linear(rgb)[..., 1], relative_luminance(rgb), rtol=0, atol=1e-12
    )


def test_xyz_keeps_leading_shape() -> None:
    """SPEC 5.3: XYZ is computed per cell, so [..., 3] -> [..., 3]."""
    assert xyz_from_linear(np.zeros((2, 3, 3))).shape == (2, 3, 3)


# ---------------------------------------------------------------------------
# uv_chromaticity
# ---------------------------------------------------------------------------


def test_uv_of_red() -> None:
    """SPEC 3.6: u' = 4X/(X+15Y+3Z), v' = 9Y/(...) gives (0.4508, 0.5229) for sRGB red."""
    uv = uv_chromaticity(_lin(255, 0, 0))
    assert uv.shape == (2,)
    assert uv[0] == pytest.approx(_RED_UV[0], abs=_UV_TOL)
    assert uv[1] == pytest.approx(_RED_UV[1], abs=_UV_TOL)


def test_uv_of_black_is_the_d65_constant() -> None:
    """ADR-011: X+15Y+3Z == 0 for black, so the engine returns D65 (u', v') exactly."""
    uv = uv_chromaticity(_lin(0, 0, 0))
    assert uv[0] == D65_UV[0]
    assert uv[1] == D65_UV[1]


def test_d65_constant_is_computed_from_the_matrix() -> None:
    """ADR-011: D65 is (0.1978, 0.4683) to four decimals; white computed from the matrix gives it bit for bit."""
    assert D65_UV[0] == pytest.approx(0.1978, abs=_UV_TOL)
    assert D65_UV[1] == pytest.approx(0.4683, abs=_UV_TOL)
    np.testing.assert_array_equal(uv_chromaticity(_lin(255, 255, 255)), np.array(D65_UV))


def test_black_and_white_have_the_same_chromaticity() -> None:
    """ADR-011: black counts as colourless exactly like white, so their (u', v') distance is 0, not 5e-5."""
    black = uv_chromaticity(_lin(0, 0, 0))
    white = uv_chromaticity(_lin(255, 255, 255))
    assert math.hypot(*(black - white)) == 0.0


def test_uv_is_independent_of_intensity() -> None:
    """SPEC 3.6: chromaticity is a ratio of tristimulus values, so grey equals white."""
    grey = uv_chromaticity(np.array([0.2, 0.2, 0.2]))
    white = uv_chromaticity(np.array([1.0, 1.0, 1.0]))
    np.testing.assert_allclose(grey, white, rtol=0, atol=1e-12)


def test_uv_shape_is_leading_shape_plus_two() -> None:
    """SPEC 5.3: uv[t, y, x] holds two numbers per cell, so [..., 3] -> [..., 2]."""
    out = uv_chromaticity(np.full((3, 2, 3), 0.5))
    assert out.shape == (3, 2, 2)
    assert out.dtype == np.float64


def test_uv_black_guard_is_elementwise() -> None:
    """ADR-011: only the black cells fall back to D65; red cells in the same array keep their uv."""
    rgb = np.stack([_lin(0, 0, 0), _lin(255, 0, 0)])
    uv = uv_chromaticity(rgb)
    np.testing.assert_array_equal(uv[0], np.array(D65_UV, dtype=np.float64))
    assert uv[1, 0] == pytest.approx(_RED_UV[0], abs=_UV_TOL)
    assert uv[1, 1] == pytest.approx(_RED_UV[1], abs=_UV_TOL)


def test_red_to_black_uv_distance_exceeds_0_2_but_red_to_orange_does_not() -> None:
    """ADR-011: red<->black is 0.259 (> 0.2, a red transition); red<->orange (255,128,0) is 0.13."""
    red = uv_chromaticity(_lin(255, 0, 0))
    black = uv_chromaticity(_lin(0, 0, 0))
    orange = uv_chromaticity(_lin(255, 128, 0))
    assert math.hypot(*(red - black)) == pytest.approx(0.259, abs=0.002)
    assert math.hypot(*(red - black)) > 0.2
    assert math.hypot(*(red - orange)) == pytest.approx(0.13, abs=0.005)
    assert math.hypot(*(red - orange)) < 0.2


@_PROPERTY
@given(st.lists(st.floats(0.0, 1.0), min_size=3, max_size=3))
def test_uv_is_always_finite(rgb: list[float]) -> None:
    """ADR-011: the zero-denominator case is guarded, so uv never produces NaN or inf."""
    uv = uv_chromaticity(np.array(rgb, dtype=np.float64))
    assert np.all(np.isfinite(uv))


# ---------------------------------------------------------------------------
# redness_ratio
# ---------------------------------------------------------------------------


def test_redness_ratio_of_pure_red_is_one() -> None:
    """SPEC T3 / ADR-003: R8/(R8+G8+B8) is 255/255 = 1.0 exactly for pure red."""
    assert redness_ratio(_lin(255, 0, 0)) == 1.0


def test_redness_ratio_255_64_0_is_not_saturated() -> None:
    """SPEC T3: 255/(255+64) = 0.7994 is below the 0.8 saturation line."""
    ratio = redness_ratio(_lin(255, 64, 0))
    assert ratio == 255 / 319
    assert ratio < 0.8


def test_redness_ratio_255_63_0_is_saturated() -> None:
    """SPEC T3: 255/(255+63) = 0.8019 is 'greater than or equal to 0.8'."""
    ratio = redness_ratio(_lin(255, 63, 0))
    assert ratio == 255 / 318
    assert ratio >= 0.8


@pytest.mark.parametrize("rgb", [(204, 51, 0), (16, 4, 0), (240, 30, 30), (136, 4, 30), (12, 3, 0)])
def test_redness_ratio_exactly_0_8_is_exactly_0_8(rgb: tuple[int, int, int]) -> None:
    """ADR-020: on whole 0-255 values R/(R+G+B) = 4k/5k is exactly 0.8 (not 0.7999999999999999).

    (12, 3, 0) is the one exception: R = 12 is below RED_MIN_R8, so its ratio is 0 (ADR-020).
    """
    expected = 0.0 if rgb[0] < RED_MIN_R8 else 0.8
    assert redness_ratio(_lin(*rgb)) == expected


def test_redness_ratio_205_52_0_is_not_saturated() -> None:
    """SPEC 5.10 item 12: 205/257 = 0.7977 is below 0.8."""
    assert redness_ratio(_lin(205, 52, 0)) == 205 / 257
    assert redness_ratio(_lin(205, 52, 0)) < RED_SATURATION_MIN_RATIO


def test_reds_darker_than_16_have_ratio_zero() -> None:
    """ADR-020: R8 = 1..15 has no visible hue, so the ratio is 0; R8 = 16 is the first red that counts."""
    dark = np.stack([_lin(r, 0, 0) for r in range(1, 16)])
    assert not redness_ratio(dark).any()
    assert redness_ratio(_lin(16, 0, 0)) == 1.0


def test_red_floor_is_the_smallest_red_the_legacy_rule_could_count() -> None:
    """ADR-020: 16 is the smallest R8 with R8/255 x 320 > 20, the pre-2022 'greater than 20' against black."""
    assert RED_MIN_R8 == 16
    assert RED_MIN_R8 / 255 * LEGACY_RED_VALUE_SCALE > LEGACY_RED_MIN_DELTA
    assert (RED_MIN_R8 - 1) / 255 * LEGACY_RED_VALUE_SCALE <= LEGACY_RED_MIN_DELTA


def test_redness_ratio_of_black_is_zero() -> None:
    """ADR-003: 'Black (sum 0) has ratio 0' instead of dividing by zero."""
    assert redness_ratio(_lin(0, 0, 0)) == 0.0


def test_redness_ratio_of_white_is_one_third() -> None:
    """ADR-003: equal encoded channels give R/(R+G+B) = 1/3."""
    assert redness_ratio(_lin(255, 255, 255)) == 1 / 3


def test_redness_ratio_uses_encoded_not_linear_components() -> None:
    """ADR-003: the ratio is taken on sRGB-encoded values; on linear values (255,64,0) would be ~0.95."""
    ratio = redness_ratio(_lin(255, 64, 0))
    linear_ratio = 1.0 / (1.0 + _wcag_linear(64))
    assert abs(ratio - linear_ratio) > 0.1


def test_redness_ratio_drops_the_channel_axis() -> None:
    """SPEC 5.3: ratio[t, y, x] is one number per cell."""
    out = redness_ratio(np.full((2, 3, 3), 0.25))
    assert out.shape == (2, 3)
    assert out.dtype == np.float64


@_PROPERTY
@given(st.lists(st.floats(0.0, 1.0), min_size=3, max_size=3))
def test_redness_ratio_stays_inside_unit_interval(rgb: list[float]) -> None:
    """SPEC T3: a share of a sum of non-negative numbers lies in 0..1."""
    ratio = float(redness_ratio(np.array(rgb, dtype=np.float64)))
    assert 0.0 <= ratio <= 1.0 + 1e-12


# ---------------------------------------------------------------------------
# legacy_red_value (comparison mode only, ADR-001)
# ---------------------------------------------------------------------------


def test_legacy_red_value_of_pure_red_is_320() -> None:
    """ADR-001 legacy rule: (R-G-B) x 320 gives 255/255 x 320 = 320 exactly for pure red."""
    assert legacy_red_value(_lin(255, 0, 0)) == 320.0


def test_legacy_red_value_of_half_red() -> None:
    """ADR-001 / ADR-020 legacy rule: (128 - 0 - 0) / 255 x 320 = 160.63, computed on whole 0-255 values."""
    assert legacy_red_value(_lin(128, 0, 0)) == 128 / 255 * 320


def test_legacy_red_value_uses_whole_values() -> None:
    """ADR-020: (200, 50, 30) gives (200 - 50 - 30) / 255 x 320 exactly."""
    assert legacy_red_value(_lin(200, 50, 30)) == 120 / 255 * 320


def test_legacy_red_value_of_yellow_is_zero() -> None:
    """ADR-001 legacy rule: R-G-B = 1-1-0 = 0 for yellow, so the value is 0."""
    assert legacy_red_value(_lin(255, 255, 0)) == 0.0


def test_legacy_red_value_clips_negatives_to_zero() -> None:
    """ADR-001 legacy rule: max(0, R-G-B), so blue (R-G-B = -1) gives 0, not -320."""
    assert legacy_red_value(_lin(0, 0, 255)) == 0.0


def test_legacy_red_value_drops_the_channel_axis() -> None:
    """SPEC 5.3: RV[t, y, x] is one number per cell."""
    out = legacy_red_value(np.zeros((3, 2, 3)))
    assert out.shape == (3, 2)
    assert out.dtype == np.float64


@_PROPERTY
@given(st.lists(st.floats(0.0, 1.0), min_size=3, max_size=3))
def test_legacy_red_value_stays_between_0_and_320(rgb: list[float]) -> None:
    """ADR-001 legacy rule: clipped at 0 below and bounded by 1 x 320 above."""
    rv = float(legacy_red_value(np.array(rgb, dtype=np.float64)))
    assert 0.0 <= rv <= 320.0 + 1e-9


# ---------------------------------------------------------------------------
# Linear-light cell averaging (ADR-002, the engine/cells.py module docstring)
# ---------------------------------------------------------------------------


def test_half_black_half_white_cell_is_l_0_5_not_the_byte_average() -> None:
    """ADR-002: a 4x4 cell of 8 white and 8 black pixels measures L = 0.500, while linearising the
    byte average 127.5 would give 0.214 - the figure the engine/cells.py docstring quotes."""
    from engine.cells import cell_signals, frame_to_cells

    frame = np.zeros((4, 4, 3), dtype=np.uint8)
    frame[:2] = 255  # two rows of white over two rows of black
    assert cell_signals(frame_to_cells(frame)).L.tolist() == [0.5]
    # WCAG 2.2 glossary: "R = ((RsRGB+0.055)/1.055) ^ 2.4" with RsRGB = 127.5/255 = 0.5, and the
    # three luminance coefficients add up to 1 for a grey.
    byte_average_l = ((0.5 + 0.055) / 1.055) ** 2.4
    assert byte_average_l == pytest.approx(0.214, abs=5e-4)
