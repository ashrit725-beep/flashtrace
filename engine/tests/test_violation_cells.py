"""AnalysisResult.violation_cells: which cells flashed in each violation (input to attribution, SPEC 7).

One bool [cells_y, cells_x] mask per violation, in the order of ``violations``: the union of the event
masks E[t] = M[t-1] | M[t] over the violation's event frames (SPEC 5.6 step 1).  The masks are a
by-product only: verdicts, counts and the report JSON must be exactly what they were without them.
"""
from __future__ import annotations

import json

import numpy as np

from engine import analyze
from engine.constants import CELL_PX
from engine.tests import synth
from engine.tests.synth import BLACK, GREY_127, RED, WHITE, Region


def region_mask(region: Region, grid: tuple[int, int]) -> np.ndarray:
    """The cells a grid-aligned region covers, as bool [cells_y, cells_x]."""
    mask = np.zeros(grid, dtype=bool)
    mask[region.y // CELL_PX:(region.y + region.h) // CELL_PX, region.x // CELL_PX:(region.x + region.w) // CELL_PX] = True
    return mask


def run(regions: list[Region], duration_s: float = 1.5):
    frames = synth.scene_frames(WHITE, regions, duration_s)
    return analyze(frames, fps=synth.FPS, viewport=(synth.VIEWPORT_W, synth.VIEWPORT_H))


def test_one_violation_masks_exactly_the_flashing_region() -> None:
    # 200 x 200 px = 2 500 cells: more than 25% of the 5 440-cell window, so a 4 Hz flash fails.
    flasher = Region(40, 40, 200, 200, synth.square_wave(4, BLACK, WHITE))
    result = run([flasher])
    assert result.verdict == "FAIL" and len(result.violations) == 1
    assert len(result.violation_cells) == 1
    cells = result.violation_cells[0]
    assert cells.dtype == bool and cells.shape == result.grid
    assert np.array_equal(cells, region_mask(flasher, result.grid))


def test_general_and_red_violations_get_their_own_masks_in_violation_order() -> None:
    # Red <-> equal-luminance grey is a red flash only (SPEC 5.10 item 6); black <-> white is general only.
    red_only = Region(40, 40, 200, 200, synth.square_wave(4, RED, GREY_127))
    general_only = Region(600, 400, 200, 200, synth.square_wave(4, BLACK, WHITE))
    result = run([red_only, general_only])
    assert [v.type for v in result.violations] == ["general", "red"]
    assert np.array_equal(result.violation_cells[0], region_mask(general_only, result.grid))
    assert np.array_equal(result.violation_cells[1], region_mask(red_only, result.grid))


def test_pass_has_no_masks_and_the_report_json_is_unchanged() -> None:
    small = Region(40, 40, 40, 40, synth.square_wave(4, BLACK, WHITE))  # 100 cells: far below 25%
    result = run([small])
    assert result.verdict == "PASS" and result.violation_cells == []
    failing = run([Region(40, 40, 200, 200, synth.square_wave(4, BLACK, WHITE))])
    data = failing.to_dict()
    assert "violationCells" not in data and "violation_cells" not in data
    json.dumps(data)  # still JSON-safe: the numpy masks never reach to_dict()


def test_masks_are_deterministic() -> None:
    regions = [Region(40, 40, 200, 200, synth.square_wave(4, BLACK, WHITE))]
    first, second = run(regions), run(regions)
    assert len(first.violation_cells) == len(second.violation_cells) == 1
    assert np.array_equal(first.violation_cells[0], second.violation_cells[0])
