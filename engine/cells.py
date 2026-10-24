"""Cells: shrink a frame to a grid of 4x4 px averages and read the signals off it.

Why cells at all: WCAG thresholds are about regions of the screen, not single
pixels, and a 1024x768 frame has 786 432 pixels.  Averaging each 4x4 block
into one colour gives 49 152 cells, which is cheap to track over hundreds of
frames and still fine enough to measure the 25 % area rule precisely.

The average is taken in linear light (ADR-002): luminance is linear in light,
so a cell's L is exactly the mean of its pixels' L, which is what the
threshold is about.  Averaging the encoded bytes instead would be wrong by up
to several percent.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from engine.color import (
    legacy_red_value,
    redness_ratio,
    relative_luminance,
    srgb8_to_linear,
    uv_chromaticity,
)
from engine.constants import CELL_PX, REFERENCE_VIEWPORT, WINDOW_PX


def grid_shape(height: int, width: int, cell_px: int = CELL_PX) -> tuple[int, int]:
    """How many whole cells fit: (cells_y, cells_x).  Leftover pixels are dropped."""
    if cell_px < 1:
        raise ValueError(f"cell_px must be >= 1, got {cell_px}")
    # Floor division: a partial cell at the right or bottom edge (at most 3 px)
    # is not analysed (ADR-002) rather than being padded with made-up pixels.
    return height // cell_px, width // cell_px


def frame_to_cells(frame_u8: np.ndarray, cell_px: int = CELL_PX) -> np.ndarray:
    """uint8 frame [H, W, 3] -> mean LINEAR rgb per cell, float64 [cells_y, cells_x, 3]."""
    frame = np.asarray(frame_u8)
    if frame.ndim != 3 or frame.shape[2] != 3:
        raise ValueError(f"expected a frame shaped [H, W, 3], got {frame.shape}")
    cells_y, cells_x = grid_shape(frame.shape[0], frame.shape[1], cell_px)
    if cells_y == 0 or cells_x == 0:
        raise ValueError(f"frame {frame.shape[:2]} is smaller than one {cell_px}x{cell_px} cell")
    # 1. Crop to whole cells.  2. Linearise every pixel through the lookup
    # table.  3. Add up the cell_px pixel rows of every cell row (the image
    # becomes cells_y rows of full-width sums), then add up the cell_px pixel
    # columns of every cell.  4. Divide once by the number of pixels per cell.
    # Rows first then columns is 3-4x faster than one combined reduction and
    # is still a fixed operation order, so the result is reproducible bit for bit.
    cropped = frame[: cells_y * cell_px, : cells_x * cell_px, :]
    linear = srgb8_to_linear(cropped)
    row_sums = linear.reshape(cells_y, cell_px, cells_x * cell_px, 3).sum(axis=1)
    cell_sums = row_sums.reshape(cells_y, cells_x, cell_px, 3).sum(axis=2)
    return cell_sums / float(cell_px * cell_px)


def _window_scale(viewport_width: int) -> float:
    """Width ratio against the 1024 px reference viewport (ADR-007)."""
    if viewport_width < 1:
        raise ValueError(f"viewport width must be >= 1, got {viewport_width}")
    # WCAG 2.2 glossary Note 1 fixes the window for "1024 x 768 pixels"; other
    # widths scale it proportionally, which the report records as an assumption.
    return viewport_width / REFERENCE_VIEWPORT[0]


def window_px(viewport_width: int) -> tuple[int, int]:
    """The 10-degree window in CSS px for this viewport width: (w_px, h_px)."""
    scale = _window_scale(viewport_width)
    # WCAG 2.2 glossary Note 1: "a 341 x 256 pixel rectangle"
    return int(round(WINDOW_PX[0] * scale)), int(round(WINDOW_PX[1] * scale))


def window_cells(
    viewport_width: int, cells_y: int, cells_x: int, cell_px: int = CELL_PX
) -> tuple[int, int]:
    """The same window measured in cells, (wy, wx), clamped to the grid.

    For 1024 px wide: 256/4 = 64 rows, 341/4 = 85.25 -> 85 columns (ADR-006
    records that this makes the window 0.3 % smaller than 341x256 px).
    """
    scale = _window_scale(viewport_width)
    wy = int(round(WINDOW_PX[1] * scale / cell_px))
    wx = int(round(WINDOW_PX[0] * scale / cell_px))
    # A window can never be smaller than one cell or larger than the grid.
    wy = min(max(wy, 1), cells_y)
    wx = min(max(wx, 1), cells_x)
    return wy, wx


@dataclass
class CellSignals:
    """The five per-cell numbers the transition rules look at, each float64[N].

    N = cells_y * cells_x, flattened row by row (row-major), so index
    i = y * cells_x + x.
    """

    L: np.ndarray      # relative luminance (general flash signal)
    ratio: np.ndarray  # R / (R + G + B) on encoded components (saturated-red test)
    u: np.ndarray      # CIE 1976 u' (red flash, WCAG 2.2 rule)
    v: np.ndarray      # CIE 1976 v'
    rv: np.ndarray     # legacy red value max(0, R - G - B) x 320 (comparison mode)


def cell_signals(cells_rgb: np.ndarray) -> CellSignals:
    """Mean linear rgb per cell [cells_y, cells_x, 3] -> CellSignals over N cells."""
    rgb = np.asarray(cells_rgb, dtype=np.float64)
    if rgb.ndim != 3 or rgb.shape[2] != 3:
        raise ValueError(f"expected cells shaped [cells_y, cells_x, 3], got {rgb.shape}")
    flat = rgb.reshape(-1, 3)
    uv = uv_chromaticity(flat)
    return CellSignals(
        L=relative_luminance(flat),
        ratio=redness_ratio(flat),
        u=uv[:, 0],
        v=uv[:, 1],
        rv=legacy_red_value(flat),
    )
