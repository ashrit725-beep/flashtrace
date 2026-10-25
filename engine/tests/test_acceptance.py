"""Acceptance tests for docs/SPEC.md section 5.10 (items 1-19; items 17-19 pin the end-of-capture,
near-black and pixel-area rules of ADR-042, ADR-043 and ADR-044), plus the input-validation and
report-shape checks of the engine contract.

Black box: only ``engine.analyze`` and the result object it returns are used (item 12 also
reads the per-cell redness ratio through ``engine.cells``).  Frames come from
``engine/tests/synth.py`` and are streamed, never stacked.  A PASS here means "no 2.3.1
violation detected under the tested conditions", nothing more.

Counting convention (SPEC 5.5, ADR-005): a flash is counted when its SECOND transition
completes, and the qualifying event is counted on that completing frame (SPEC 5.6, ADR-019).
A 4 Hz square wave at 60 fps therefore completes one flash every 15 frames, so a window of
60 frames holds up to 4 of them; a 3 Hz wave completes one every 20 frames and a window holds
3.  The tests assert on verdicts, maximum rates, counts and whole-array equality; only the
evidence-frame test looks at which frames were picked, and then only at what they show.
"""
from __future__ import annotations

import base64
import json
from typing import Any, Sequence

import numpy as np
import pytest

from engine import AnalysisResult, analyze
from engine.analyze import evidence_frame_indices
from engine.cells import cell_signals, frame_to_cells
from engine.constants import RED_MIN_R8, RED_SATURATION_MIN_RATIO
from engine.tests import synth
from engine.tests.synth import (
    BLACK,
    DARK_RED,
    GREY_124,
    GREY_127,
    GREY_147,
    GREY_235,
    RED,
    RGB,
    WHITE,
    Region,
    full_screen,
    square_wave,
    square_wave_exact,
    square_wave_state,
)

# 1.5 s at 60 fps = 90 frames.  The one-second rate window is 60 frames, so 90 frames contain
# 31 window positions: enough to measure the per-second rate, short enough to keep each test fast.
DURATION_S: float = 1.5
VIEWPORT: tuple[int, int] = (synth.VIEWPORT_W, synth.VIEWPORT_H)

FOUR_HZ: float = 4.0   # "more than three" flashes per second -> must FAIL when large enough
THREE_HZ: float = 3.0  # exactly three per second -> allowed

# Window and grid geometry for a 1024x768 capture, derived exactly as SPEC section 4 does:
# 341/4 = 85.25 -> 85 cells wide, 256/4 = 64 cells high, 85 * 64 = 5440 cells per window.
# "no more than ... 25%" means 1360 cells pass and 1361 fail (ADR-006).
WINDOW_CELLS_X: int = 85
WINDOW_CELLS_Y: int = 64
WINDOW_CELLS: int = WINDOW_CELLS_X * WINDOW_CELLS_Y  # 5440
GRID: tuple[int, int] = (synth.VIEWPORT_H // synth.CELL_PX, synth.VIEWPORT_W // synth.CELL_PX)  # (192, 256)

# Keys that AnalysisResult.to_dict() must contain (engine contract).
CONTRACT_KEYS = {
    "verdict", "reason", "violations", "advisory", "timeline", "heatmap",
    "generalMaxRatePerSec", "redMaxRatePerSec", "qualifyingGeneralFrames", "qualifyingRedFrames",
    "windowPx", "grid", "redRule", "engineVersion", "frameCount", "fps",
}


# ---------------------------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------------------------
def run(regions: Sequence[Region], *, background: RGB = BLACK, duration_s: float = DURATION_S,
        **kwargs: Any) -> AnalysisResult:
    """Analyse a scene through the public entry point, streaming the frames."""
    frames = synth.scene_frames(background, regions, duration_s)
    return analyze(frames, fps=synth.FPS, viewport=VIEWPORT, **kwargs)


def flashing_region(x: int, y: int, w: int, h: int, hz: float = FOUR_HZ,
                    colour_a: RGB = BLACK, colour_b: RGB = WHITE) -> Region:
    """A rectangle toggling between two colours, black <-> white at 4 Hz by default."""
    return Region(x, y, w, h, square_wave(hz, colour_a, colour_b))


def violation_types(result: AnalysisResult) -> list[str]:
    """The type ("general" or "red") of every violation, in report order."""
    return [v.type for v in result.violations]


def violation_summary(result: AnalysisResult) -> list[tuple[int, int, str, int, list[int]]]:
    """Everything about a violation except its window position (used for invariance checks)."""
    return [(v.start, v.end, v.type, v.max_rate_per_sec, list(v.frame_indices)) for v in result.violations]


# Two scenes are shared by several tests; analyse each once per module.
@pytest.fixture(scope="module")
def four_hz_full_screen() -> AnalysisResult:
    return run([full_screen(square_wave(FOUR_HZ, BLACK, WHITE))])


@pytest.fixture(scope="module")
def region_26pct_at_origin() -> AnalysisResult:
    # 236 x 96 px = 59 x 24 cells = 1416 cells = 26.03% of a 5440-cell window.
    return run([flashing_region(0, 0, 236, 96)])


@pytest.fixture(scope="module")
def static_page() -> AnalysisResult:
    # Nothing changes, so nothing can flash.  0.5 s (30 frames) is shorter than one rate window.
    return run([full_screen(synth.constant(GREY_127))], duration_s=0.5)


# ---------------------------------------------------------------------------------------------
# Fixture self-check: the colour arithmetic documented in synth.py must be true.
# ---------------------------------------------------------------------------------------------
def test_fixture_colours_match_their_documented_luminance() -> None:
    """The luminance claims in synth.py, re-computed with the WCAG formula (no engine involved)."""
    L = synth.wcag_relative_luminance
    assert L(BLACK) == 0.0
    assert L(WHITE) == pytest.approx(1.0)
    assert L(RED) == pytest.approx(0.2126)
    assert abs(L(RED) - L(GREY_127)) < 0.001          # 0.0004: an equal-luminance pair (item 6)
    assert 0.085 < L(GREY_147) - L(GREY_124) < 0.10    # 0.0902: just under the 10% step (item 4)
    assert L(GREY_235) > 0.80                           # 0.8308: both states of item 5 are >= 0.80
    assert L(WHITE) - L(GREY_235) >= 0.10               # 0.169: the step itself is large enough
    assert L(RED) - L(DARK_RED) >= 0.10                 # 0.1667: item 10 is a general flash


# ---------------------------------------------------------------------------------------------
# SPEC 5.10 items 1-10
# ---------------------------------------------------------------------------------------------
def test_01_four_hz_full_screen_black_white_fails(four_hz_full_screen: AnalysisResult) -> None:
    """SPEC 5.10 item 1: 4 Hz full-screen square wave (black <-> white) => FAIL, maxRatePerSec == 4."""
    r = four_hz_full_screen
    assert r.verdict == "FAIL"
    assert r.general_max_rate == 4
    # Black has R/(R+G+B) = 0 and white 1/3: neither state is a saturated red, so no red flash.
    assert r.red_max_rate == 0
    assert violation_types(r) == ["general"]
    v = r.violations[0]
    assert v.max_rate_per_sec == 4
    assert 0 <= v.start <= v.end < r.frame_count
    assert len(v.frame_indices) >= 4
    assert all(v.start <= f <= v.end for f in v.frame_indices)
    # The failing window is reported in CSS px and lies inside the viewport.
    assert v.window.x >= 0 and v.window.y >= 0 and v.window.w > 0 and v.window.h > 0
    assert v.window.x + v.window.w <= synth.VIEWPORT_W
    assert v.window.y + v.window.h <= synth.VIEWPORT_H
    # Engine contract: reason() is a method returning one sentence (verdict is a property).
    reason = r.reason()
    assert isinstance(reason, str) and reason
    assert "compliant" not in reason.lower()


def test_02_three_hz_full_screen_passes() -> None:
    """SPEC 5.10 item 2: 3 Hz full-screen square wave => PASS (three per second is not "more than three")."""
    r = run([full_screen(square_wave(THREE_HZ, BLACK, WHITE))])
    assert r.verdict == "PASS"
    assert r.general_max_rate == 3
    assert r.red_max_rate == 0
    assert r.violations == []
    # A PASS is a non-detection, never a certification.  (reason() is a method per the contract.)
    reason = r.reason()
    assert "no 2.3.1 violation detected under the tested conditions" in reason.lower()
    assert "compliant" not in reason.lower()


def test_03_region_covering_20_percent_of_a_window_passes() -> None:
    """SPEC 5.10 item 3 (first half): 4 Hz square wave covering 20% of one window => PASS."""
    region = flashing_region(0, 0, 136, 128)  # 34 x 32 cells
    assert region.cells == 1088 and region.cells / WINDOW_CELLS == pytest.approx(0.20)
    r = run([region])
    assert r.verdict == "PASS"
    assert r.general_max_rate == 0           # the flashes are real but never qualify by area
    assert r.qualifying_general == []
    assert r.violations == []
    assert r.frame_stats.general_area_max.max() == pytest.approx(1088 / WINDOW_CELLS)


def test_03_region_covering_26_percent_of_a_window_fails(region_26pct_at_origin: AnalysisResult) -> None:
    """SPEC 5.10 item 3 (second half): 4 Hz square wave covering 26% of one window => FAIL."""
    r = region_26pct_at_origin
    assert r.verdict == "FAIL"
    assert r.general_max_rate == 4
    assert violation_types(r) == ["general"]
    assert r.frame_stats.general_area_max.max() == pytest.approx(1416 / WINDOW_CELLS)


def test_03_region_covering_exactly_25_percent_passes() -> None:
    """SPEC 5.10 item 3 boundary: "no more than ... 25%" means exactly 25% of a window still passes."""
    region = flashing_region(0, 0, 340, 64)  # 85 x 16 cells: the full window width, a quarter of its height
    assert region.cells == 1360 and 1360 / WINDOW_CELLS == 0.25
    r = run([region])
    assert r.verdict == "PASS"
    assert r.general_max_rate == 0
    assert r.qualifying_general == []
    assert r.frame_stats.general_area_max.max() == pytest.approx(0.25)


def test_04_luminance_step_of_0_09_passes() -> None:
    """SPEC 5.10 item 4: Delta L = 0.09 => PASS (no transition, "10% or more" is required)."""
    r = run([full_screen(square_wave(FOUR_HZ, GREY_124, GREY_147))])
    assert r.verdict == "PASS"
    assert r.general_max_rate == 0
    assert r.red_max_rate == 0
    assert r.violations == []
    assert not r.heatmap.any()  # no cell completed a flash of either type


def test_05_both_states_above_0_80_pass() -> None:
    """SPEC 5.10 item 5: both states above L 0.80 with Delta L >= 0.10 => PASS (darker image not "below 0.80")."""
    r = run([full_screen(square_wave(FOUR_HZ, GREY_235, WHITE))])
    assert r.verdict == "PASS"
    assert r.general_max_rate == 0
    assert r.red_max_rate == 0
    assert r.violations == []
    assert not r.heatmap.any()


def test_06_saturated_red_against_equal_luminance_grey_fails_by_red_rule_only() -> None:
    """SPEC 5.10 item 6: saturated red toggling at 4 Hz against an equal-luminance grey => FAIL via the red rule, no general violation."""
    r = run([full_screen(square_wave(FOUR_HZ, RED, GREY_127))])
    assert r.verdict == "FAIL"
    assert r.red_max_rate == 4
    assert r.general_max_rate == 0           # Delta L = 0.0004, far below 0.10
    assert r.qualifying_general == []
    assert len(r.qualifying_red) >= 4
    assert violation_types(r) == ["red"]
    assert r.violations[0].max_rate_per_sec == 4


# Item 7 geometry: two halves of 116 x 112 px = 29 x 28 cells = 812 cells each (14.9% of a window).
# Together they cover 1624 cells, more than the 1360-cell quarter, but only when one window
# position contains both of them.
HALF_W: int = 116
HALF_H: int = 112
HALF_CELLS: int = 812


def two_halves(gap_px: int) -> list[Region]:
    left = flashing_region(0, 128, HALF_W, HALF_H)
    right = flashing_region(HALF_W + gap_px, 128, HALF_W, HALF_H)
    assert left.cells == right.cells == HALF_CELLS
    return [left, right]


def test_07_split_region_inside_one_window_fails() -> None:
    """SPEC 5.10 item 7: a region split across two positions that exceeds 25% only when one window contains both parts => FAIL."""
    gap_px = 60
    assert HALF_W + gap_px + HALF_W <= 341   # 292 px: a single 341 px wide window spans both halves
    r = run(two_halves(gap_px))
    assert r.verdict == "FAIL"
    assert r.general_max_rate == 4
    assert violation_types(r) == ["general"]
    assert r.frame_stats.general_area_max.max() == pytest.approx(2 * HALF_CELLS / WINDOW_CELLS)


def test_07_split_region_in_separate_windows_passes() -> None:
    """SPEC 5.10 item 7: the same two halves too far apart for any window to contain both => PASS."""
    gap_px = 400
    assert gap_px // synth.CELL_PX > WINDOW_CELLS_X  # a 100-cell gap is wider than the 85-cell window
    r = run(two_halves(gap_px))
    assert r.verdict == "PASS"
    assert r.general_max_rate == 0
    assert r.violations == []
    assert r.frame_stats.general_area_max.max() == pytest.approx(HALF_CELLS / WINDOW_CELLS)
    # Both halves still flash; the heatmap counts completed flashes whether or not they qualify.
    assert int((r.heatmap > 0).sum()) == 2 * HALF_CELLS


def test_07_each_half_alone_passes() -> None:
    """SPEC 5.10 item 7: each half alone (14.9% of a window) => PASS."""
    r = run([flashing_region(0, 128, HALF_W, HALF_H)])
    assert r.verdict == "PASS"
    assert r.general_max_rate == 0
    assert r.violations == []


def test_08_translation_invariance(region_26pct_at_origin: AnalysisResult) -> None:
    """SPEC 5.10 item 8: the same region at two offsets gives identical verdict, counts and area."""
    a = region_26pct_at_origin
    b = run([flashing_region(600, 400, 236, 96)])  # 600 + 236 <= 1024, 400 + 96 <= 768, grid aligned
    assert a.verdict == b.verdict == "FAIL"
    assert a.general_max_rate == b.general_max_rate
    assert a.red_max_rate == b.red_max_rate
    assert list(a.qualifying_general) == list(b.qualifying_general)
    assert np.array_equal(a.frame_stats.general_area_max, b.frame_stats.general_area_max)
    assert np.array_equal(a.frame_stats.general_cells, b.frame_stats.general_cells)
    assert np.array_equal(a.frame_stats.general_trailing_1s, b.frame_stats.general_trailing_1s)
    assert np.array_equal(a.frame_stats.general_qualifying, b.frame_stats.general_qualifying)
    assert violation_summary(a) == violation_summary(b)
    # The heatmap moved with the region: 59 x 24 hot cells at (0, 0) and at cell (150, 100).
    assert int((a.heatmap > 0).sum()) == int((b.heatmap > 0).sum()) == 1416
    assert np.array_equal(a.heatmap[0:24, 0:59], b.heatmap[100:124, 150:209])


def test_09_determinism_same_input_twice_gives_identical_output() -> None:
    """SPEC 5.10 item 9: the same input analysed twice yields identical outputs (array equality and identical JSON)."""

    def scene() -> list[Region]:
        general = flashing_region(0, 0, 236, 96)                           # 26% black <-> white
        red = Region(512, 384, 236, 96, square_wave(FOUR_HZ, RED, GREY_127))  # 26% red <-> grey
        return [general, red]

    first = run(scene())
    second = run(scene())
    assert json.dumps(first.to_dict()) == json.dumps(second.to_dict())            # same keys, same order
    assert json.dumps(first.to_dict(), sort_keys=True) == json.dumps(second.to_dict(), sort_keys=True)
    assert np.array_equal(first.heatmap, second.heatmap)
    assert np.array_equal(first.frame_stats.general_area_max, second.frame_stats.general_area_max)
    assert np.array_equal(first.frame_stats.red_area_max, second.frame_stats.red_area_max)
    assert first.verdict == second.verdict == "FAIL"


def test_10_red_to_dark_red_is_a_general_flash_only_under_wcag22() -> None:
    """SPEC 5.10 item 10: red <-> dark red (same chromaticity, Delta L >= 0.10) => general flash only, no red flash (wcag22)."""
    r = run([full_screen(square_wave(FOUR_HZ, RED, DARK_RED))], red_rule="wcag22")
    assert r.red_rule == "wcag22"
    assert r.verdict == "FAIL"
    assert r.general_max_rate == 4
    # Both states have R/(R+G+B) = 1.0 and the same (u', v'): the chromaticity difference is 0,
    # not "more than 0.2", so WCAG 2.2 does not call this a red flash.
    assert r.red_max_rate == 0
    assert r.qualifying_red == []
    assert violation_types(r) == ["general"]


def test_10_red_to_dark_red_counts_as_red_under_the_legacy_rule() -> None:
    """SPEC 5.10 item 10: the legacy (R-G-B)x320 mode counts red <-> dark red as a red flash (documents the rule difference)."""
    r = run([full_screen(square_wave(FOUR_HZ, RED, DARK_RED))], red_rule="wcag21-legacy")
    assert r.red_rule == "wcag21-legacy"
    assert r.verdict == "FAIL"
    # Legacy value (R-G-B)x320 on 0..1 encoded components: red 320, dark red 160.6, a change of
    # 159 > 20 while R/(R+G+B) = 1.0 >= 0.8.
    assert r.red_max_rate == 4
    assert r.general_max_rate == 4
    assert set(violation_types(r)) == {"general", "red"}


# ---------------------------------------------------------------------------------------------
# SPEC 5.10 items 11-14 (added with ADR-019 and ADR-020)
# ---------------------------------------------------------------------------------------------
HIGH_RATE_DURATION_S: float = 2.0  # 120 frames at 60 fps: 61 one-second window positions


def most_flashes_in_any_second(hz: int, n_frames: int, fps: int = synth.FPS) -> int:
    """Count, without the engine, the most full black -> white -> black cycles in any fps frames.

    The square wave starts black.  Its state changes are listed frame by frame; every second
    change ends a full cycle (that is the 1st+2nd, 3rd+4th, ... pairing of SPEC 5.5).  The
    count is taken over every window start t0 in [0, n_frames - fps], as SPEC 5.7 does.
    """
    states = [square_wave_state(i, hz, fps) for i in range(n_frames)]
    changes = [i for i in range(1, n_frames) if states[i] != states[i - 1]]
    cycle_ends = changes[1::2]
    return max(
        sum(1 for f in cycle_ends if t0 <= f < t0 + fps) for t0 in range(n_frames - fps + 1)
    )


@pytest.mark.parametrize("hz", [15, 20, 24, 30])
def test_11_high_rate_full_screen_strobes_fail_at_their_frequency(hz: int) -> None:
    """SPEC 5.10 item 11: full-screen black <-> white at 15, 20, 24 and 30 Hz => FAIL, maxRatePerSec == frequency.

    Near the 60 fps sampling limit flashes complete 2 or 3 frames apart; they must not merge into
    one event (ADR-019).  At 24 Hz the wave changes state on 4 of every 5 frames, so 2 cycles end
    in every 5 frames and any 60-frame window holds exactly 24.
    """
    n_frames = synth.frame_count(HIGH_RATE_DURATION_S)
    expected = most_flashes_in_any_second(hz, n_frames)
    assert expected == hz  # the constructed wave really is hz cycles in every second
    r = run([full_screen(square_wave_exact(hz, BLACK, WHITE))], duration_s=HIGH_RATE_DURATION_S)
    assert r.verdict == "FAIL"
    assert r.general_max_rate == expected
    assert r.red_max_rate == 0
    assert violation_types(r) == ["general"]
    assert r.violations[0].max_rate_per_sec == expected


def equal_luminance_grey(colour: RGB) -> RGB:
    """The grey whose WCAG relative luminance is closest to ``colour``'s (computed, not looked up)."""
    target = synth.wcag_relative_luminance(colour)
    level = min(range(256), key=lambda g: abs(synth.wcag_relative_luminance((g, g, g)) - target))
    return (level, level, level)


def uniform_cell_ratios(colours: Sequence[RGB]) -> np.ndarray:
    """The engine's per-cell redness ratio of each colour, each painted as one uniform 4x4 cell."""
    frame = np.zeros((synth.CELL_PX, synth.CELL_PX * len(colours), 3), dtype=np.uint8)
    for i, colour in enumerate(colours):
        frame[:, i * synth.CELL_PX:(i + 1) * synth.CELL_PX] = colour
    return cell_signals(frame_to_cells(frame, synth.CELL_PX)).ratio


def test_12_exact_0_8_colours_are_saturated_red() -> None:
    """SPEC 5.10 item 12: R/(R+G+B) exactly 0.8 counts as saturated red (inclusive); (205,52,0) does not."""
    ratios = uniform_cell_ratios([(204, 51, 0), (16, 4, 0), (205, 52, 0)])
    assert ratios[0] >= RED_SATURATION_MIN_RATIO   # 204 / 255 = 0.8
    assert ratios[1] >= RED_SATURATION_MIN_RATIO   # 16 / 20 = 0.8, and R = 16 is not below the floor
    assert ratios[2] < RED_SATURATION_MIN_RATIO    # 205 / 257 = 0.7977


def test_12_every_8_bit_colour_with_ratio_exactly_0_8_is_saturated() -> None:
    """SPEC 5.10 item 12, exhaustive: every 8-bit colour with R >= 16 and R = 4k, G + B = k measures >= 0.8."""
    colours = [(4 * k, g, k - g) for k in range(1, 64) for g in range(k + 1) if 4 * k >= RED_MIN_R8]
    assert len(colours) == sum(k + 1 for k in range(4, 64))  # 2070 colours (k = 4 .. 63)
    assert all(5 * r == 4 * (r + g + b) for r, g, b in colours)  # exactly 0.8 in whole numbers
    ratios = uniform_cell_ratios(colours)
    assert bool((ratios >= RED_SATURATION_MIN_RATIO).all())


def test_12_exact_0_8_red_against_equal_luminance_grey_fails_by_red_rule() -> None:
    """SPEC 5.10 item 12: (204,51,0) <-> an equal-luminance grey at 4 Hz full screen => red flash rate 4."""
    orange_red: RGB = (204, 51, 0)
    grey = equal_luminance_grey(orange_red)
    assert abs(synth.wcag_relative_luminance(orange_red) - synth.wcag_relative_luminance(grey)) < 0.01
    r = run([full_screen(square_wave(FOUR_HZ, orange_red, grey))])
    assert r.verdict == "FAIL"
    assert r.red_max_rate == 4
    assert r.general_max_rate == 0
    assert violation_types(r) == ["red"]


def test_13_near_black_red_noise_is_not_a_red_flash() -> None:
    """SPEC 5.10 item 13: rgb(0,0,0) <-> rgb(1,0,0) at 4 Hz full screen => PASS, no red flash (ADR-020)."""
    r = run([full_screen(square_wave(FOUR_HZ, BLACK, (1, 0, 0)))])
    assert r.verdict == "PASS"
    assert r.red_max_rate == 0
    assert r.general_max_rate == 0
    assert r.violations == []
    assert not r.heatmap.any()  # no cell completed a flash of either type


SHORT_SIZE: tuple[int, int] = (1024, 100)  # 256 x 25 cells: shorter than the 64-cell-high window


def run_short_page(regions: Sequence[Region]) -> AnalysisResult:
    """Analyse a scene on a 1024x100 page (frames and viewport both 1024x100)."""
    frames = synth.scene_frames(BLACK, regions, DURATION_S, size=SHORT_SIZE)
    return analyze(frames, fps=synth.FPS, viewport=SHORT_SIZE)


def test_14_short_viewport_measures_area_against_the_full_window() -> None:
    """SPEC 5.10 item 14: on a 1024x100 viewport a 4 Hz flash of 600 cells (11% of 5440) => PASS (ADR-019)."""
    region = flashing_region(0, 0, 96, 100)  # 24 x 25 cells
    assert region.cells == 600
    r = run_short_page([region])
    assert r.window_cells == (25, 85)       # the scan is clipped to the 25 cell rows ...
    assert r.verdict == "PASS"
    assert r.general_max_rate == 0
    assert r.frame_stats.general_area_max.max() == 600 / WINDOW_CELLS  # ... the denominator is not


def test_14_short_viewport_still_fails_a_large_enough_flash() -> None:
    """SPEC 5.10 item 14 counterpart: a 340x100 px strip (2125 cells, 39% of 5440) on the same page => FAIL."""
    region = flashing_region(0, 0, 340, 100)  # 85 x 25 cells
    r = run_short_page([region])
    assert r.verdict == "FAIL"
    assert r.general_max_rate == 4
    assert r.frame_stats.general_area_max.max() == region.cells / WINDOW_CELLS


# ---------------------------------------------------------------------------------------------
# SPEC 5.10 items 15-16 (ADR-038: claim rule and settled stamps)
# ---------------------------------------------------------------------------------------------
def test_15_smooth_flash_completing_over_two_frames_is_counted_once() -> None:
    """SPEC 5.10 item 15: a smooth 3 Hz pulse whose left half peaks at byte 120 and right half
    at byte 255 => PASS, maxRatePerSec == 3.

    The two halves reach their extreme byte on neighbouring frames, so each flash keeps more than 25%
    of a window on three frames in a row.  The ceil(n/2) rule counted that as two events (rate 6);
    an event now claims its completing frame and the next, so each flash counts once.  110 frames
    end on a falling run, so no flash is cut short by the end of the capture.
    """
    r = analyze(synth.split_pulse_frames(THREE_HZ, 9.52, 110), fps=synth.FPS, viewport=VIEWPORT)
    first = r.qualifying_general[0]
    cells = r.frame_stats.general_cells
    assert cells[first] > 0 and cells[first + 1] > 0   # one flash, completing on two frames ...
    assert bool((r.frame_stats.general_area_max[first:first + 3] > 0.25).all())  # ... over 25% on three
    assert r.verdict == "PASS"
    assert r.general_max_rate == 3
    assert len(r.qualifying_general) == 5                # peaks at about 19.5, 39.5, ..., 99.5


def test_16_sub_tolerance_noise_does_not_scatter_a_flash() -> None:
    """SPEC 5.10 item 16: a 4 Hz grey 60 <-> 200 square wave over 1680 cells (30.9% of a
    window), with +0/+1 byte of noise per cell per frame => FAIL, maxRatePerSec == 4, exactly as clean.

    One byte at grey 60 changes L by far less than the 0.005 reversal tolerance.  When every new
    extremum moved the stamp, the noise spread one flash's completions over up to 8 frames, no
    frame reached 25% and the page measured PASS.
    """
    region = (40, 40, 168, 160)  # x, y, w, h px: 42 x 40 cells
    noisy = analyze(synth.noisy_square_region_frames(region, 4, (60, 200), 90, seed=7),
                    fps=synth.FPS, viewport=VIEWPORT)
    clean = run([flashing_region(*region, colour_a=(60, 60, 60), colour_b=(200, 200, 200))])
    assert clean.verdict == "FAIL" and clean.general_max_rate == 4
    assert noisy.verdict == "FAIL"
    assert noisy.general_max_rate == 4
    assert noisy.frame_stats.general_area_max.max() == clean.frame_stats.general_area_max.max()
    assert clean.frame_stats.general_area_max.max() == 1680 / WINDOW_CELLS


# ---------------------------------------------------------------------------------------------
# SPEC 5.10 item 17 (ADR-042, amending ADR-004: runs cut off by the end of the capture)
# ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("hz", "phase", "easing"),
    [(3.0, 0.0, "cosine"), (3.0, 0.5, "cosine"), (3.0, 0.0, "quadratic"), (3.0, 11 / 12, "quadratic"),
     (2.9, 10 / 12, "cosine")],
)
def test_17_smooth_pulse_at_or_below_3_hz_over_a_10_s_capture_passes(hz: float, phase: float, easing: str) -> None:
    """SPEC 5.10 item 17: a smooth black <-> white page pulse at 3 Hz (and 2.9 Hz), 600 frames
    sampled at (i + 1/4) / 60 => PASS, maxRatePerSec == 3.

    SC 2.3.1 fails only "more than three times in any one second period"; a 3 Hz pulse has 3.
    At these phases the capture ends part-way down a fall.  Closing that run at the last frame
    stamped a flash there, up to a few frames before the valley it never reached inside the
    capture, and the last second counted 4.  A run still moving at the last frame is now dropped.
    """
    frames = synth.eased_pulse_strip_frames(hz, phase, 600, easing)
    r = analyze(frames, fps=synth.FPS, viewport=(synth.VIEWPORT_W, 80))
    assert r.verdict == "PASS"
    assert r.general_max_rate == 3
    assert r.violations == []
    # One flash per period (20 frames at 3 Hz, 20.7 at 2.9 Hz), never closer.
    assert int(np.diff(r.qualifying_general).min()) >= 20


def test_17_flash_completed_before_the_last_frame_still_counts() -> None:
    """SPEC 5.10 item 17 counterpart: a run that has arrived before the end is still closed by
    finish().  A 4 Hz square wave that ends on a held black state: its last flash completes at frame
    75 and the black is held to frame 79, so the 80-frame capture keeps all five flashes."""
    r = run([full_screen(square_wave(FOUR_HZ, BLACK, WHITE))], duration_s=80 / synth.FPS)
    assert r.qualifying_general == [15, 30, 45, 60, 75]
    assert r.general_max_rate == 4


# ---------------------------------------------------------------------------------------------
# SPEC 5.10 item 18 (ADR-043, amending ADR-020: near-black colours have no hue)
# ---------------------------------------------------------------------------------------------
STRIP: tuple[int, int] = (synth.VIEWPORT_W, 80)  # 20 cell rows: a full-width flash covers 31% of a window
DIM_RED: RGB = (150, 0, 0)  # L = 0.0648: against black Delta L < 0.10, so only the red rule can fire


def run_strip(regions: Sequence[Region]) -> AnalysisResult:
    """Analyse a scene on a 1024 x 80 page (quick, and a full-width flash still qualifies on area)."""
    frames = synth.scene_frames(BLACK, regions, DURATION_S, size=STRIP)
    return analyze(frames, fps=synth.FPS, viewport=STRIP)


def test_18_red_fading_smoothly_to_black_is_a_red_flash() -> None:
    """SPEC 5.10 item 18: a (150,0,0) <-> black cosine pulse at 4 Hz => FAIL via the red rule,
    red maxRatePerSec == 4, exactly like the square wave between the same colours.

    The fade passes through near-black reds such as (10,0,0): ratio 0 (ADR-020) but, before the
    amendment, still red chromaticity.  The valley of the ratio signal was taken from that first
    near-black frame, so the fall measured no colour change and the page passed."""
    smooth = analyze(synth.eased_pulse_strip_frames(FOUR_HZ, 0.0, 90, "cosine", peak=DIM_RED),
                     fps=synth.FPS, viewport=STRIP)
    square = run_strip([Region(0, 0, STRIP[0], STRIP[1], square_wave(FOUR_HZ, BLACK, DIM_RED))])
    for r in (smooth, square):
        assert r.verdict == "FAIL"
        assert r.red_max_rate == 4
        assert r.general_max_rate == 0
        assert violation_types(r) == ["red"]


def test_18_red_against_near_black_is_judged_like_red_against_black() -> None:
    """SPEC 5.10 item 18: (150,0,0) <-> (1,0,0) and <-> (2,2,2) give the same verdict as <-> (0,0,0):
    one-step noise in the black cannot decide whether a red strobe is a red flash."""
    rates = []
    for dark in [BLACK, (1, 0, 0), (2, 2, 2), (15, 0, 0)]:
        r = run_strip([Region(0, 0, STRIP[0], STRIP[1], square_wave(FOUR_HZ, dark, DIM_RED))])
        rates.append((r.verdict, r.red_max_rate, r.general_max_rate))
    assert rates == [("FAIL", 4, 0)] * 4


# ---------------------------------------------------------------------------------------------
# SPEC 5.10 item 19 (ADR-044, amending ADR-002: area measured in pixels, not whole cells)
# ---------------------------------------------------------------------------------------------
MID_PAGE: tuple[int, int] = (synth.VIEWPORT_W, 400)  # 100 cell rows: room for a 256 px tall region
SUB_CELL_OFFSETS: list[tuple[int, int]] = [(0, 0), (1, 0), (2, 2), (3, 1)]  # px added to (100, 100)
GREY_118: RGB = (118, 118, 118)
GREY_150: RGB = (150, 150, 150)  # Delta L to GREY_118 = 0.124: a cell half covered changes by only 0.062


def run_offset_region(x: int, y: int, w: int, h: int, dark: RGB, light: RGB) -> AnalysisResult:
    """A 4 Hz dark <-> light rectangle at (x, y), anywhere on the pixel grid, on a dark 1024 x 400 page."""
    region = Region(x, y, w, h, square_wave(FOUR_HZ, dark, light), grid_aligned=False)
    frames = synth.scene_frames(dark, [region], DURATION_S, size=MID_PAGE)
    return analyze(frames, fps=synth.FPS, viewport=MID_PAGE)


@pytest.mark.parametrize(
    ("w", "h", "dark", "light", "verdict"),
    [
        (172, 120, BLACK, WHITE, "PASS"),       # 20 640 px = 23.6% of 341 x 256
        (340, 64, BLACK, WHITE, "PASS"),        # 21 760 px = 24.9%
        (88, 256, GREY_118, GREY_150, "FAIL"),  # 22 528 px = 25.8%, low contrast
    ],
)
def test_19_area_verdict_does_not_depend_on_sub_cell_offsets(
    w: int, h: int, dark: RGB, light: RGB, verdict: str
) -> None:
    """SPEC 5.10 item 19: the same 4 Hz rectangle moved by 1-3 px off the 4 px cell grid keeps its
    verdict.  Counting every touched cell as whole made the first two FAIL at (102, 102) (a 2-pixel
    sliver flags a black/white cell); dropping half-covered cells whose mean changes by less than 0.10
    made the low-contrast one PASS there.  Each cell now counts the pixels that flashed."""
    for dx, dy in SUB_CELL_OFFSETS:
        r = run_offset_region(100 + dx, 100 + dy, w, h, dark, light)
        assert r.verdict == verdict, (dx, dy)
        assert r.general_max_rate == (4 if verdict == "FAIL" else 0), (dx, dy)


def test_19_area_of_a_region_inside_one_window_is_its_pixel_area_at_any_offset() -> None:
    """SPEC 5.10 item 19: a 172 x 120 px rectangle fits inside one window wherever it sits, so its
    measured area is exactly its 20 640 pixels over the 85 x 64-cell window (87 040 px) at every offset."""
    areas = {
        (dx, dy): float(run_offset_region(100 + dx, 100 + dy, 172, 120, BLACK, WHITE).frame_stats.general_area_max.max())
        for dx, dy in SUB_CELL_OFFSETS
    }
    assert set(areas.values()) == {172 * 120 / (WINDOW_CELLS * synth.CELL_PX ** 2)}


# ---------------------------------------------------------------------------------------------
# Contract extras: a static page, input validation, report shape, heatmap encoding, evidence frames
# ---------------------------------------------------------------------------------------------
def test_static_page_passes_with_nothing_flashing(static_page: AnalysisResult) -> None:
    """A page that never changes has no transitions, no flashes and a PASS verdict."""
    r = static_page
    assert r.verdict == "PASS"
    assert r.general_max_rate == 0 and r.red_max_rate == 0
    assert r.violations == [] and r.advisories == []
    assert not r.heatmap.any()
    assert r.frame_count == synth.frame_count(0.5)


def test_analyze_rejects_a_single_frame_array() -> None:
    """analyze() needs at least two frames (SPEC 5.1: T >= 2) and says so with ValueError."""
    with pytest.raises(ValueError):
        analyze(np.zeros((1, synth.VIEWPORT_H, synth.VIEWPORT_W, 3), dtype=np.uint8), fps=synth.FPS)


def test_analyze_rejects_a_single_frame_iterable() -> None:
    """The same two-frame minimum applies when frames arrive as a generator."""
    one_frame = synth.scene_frames(BLACK, [], duration_s=1 / synth.FPS)
    with pytest.raises(ValueError):
        analyze(one_frame, fps=synth.FPS)


def test_analyze_rejects_float_frames() -> None:
    """Frames must be uint8 sRGB; a float array is refused rather than silently rescaled."""
    with pytest.raises(ValueError):
        analyze(np.zeros((2, synth.VIEWPORT_H, synth.VIEWPORT_W, 3), dtype=np.float32), fps=synth.FPS)


def test_analyze_rejects_an_unknown_red_rule() -> None:
    """Only "wcag22" and "wcag21-legacy" exist (ADR-001)."""
    with pytest.raises(ValueError):
        analyze(np.zeros((2, synth.VIEWPORT_H, synth.VIEWPORT_W, 3), dtype=np.uint8),
                fps=synth.FPS, red_rule="wcag20")


def test_analyze_rejects_a_frame_width_that_differs_from_the_viewport() -> None:
    """SPEC 5.1: frames are at device scale factor 1, so a 512 px wide frame cannot be a 1024 px viewport."""
    with pytest.raises(ValueError, match="viewport width"):
        analyze(np.zeros((2, 384, 512, 3), dtype=np.uint8), fps=synth.FPS, viewport=VIEWPORT)


def test_analyze_accepts_a_viewport_matching_the_frame_width() -> None:
    """SPEC 5.1: the same frames pass validation when the viewport says 512 px wide."""
    r = analyze(np.zeros((2, 384, 512, 3), dtype=np.uint8), fps=synth.FPS, viewport=(512, 384))
    assert r.verdict == "PASS"
    assert r.viewport == (512, 384)


def test_to_dict_has_the_contract_keys_and_is_json_safe(four_hz_full_screen: AnalysisResult) -> None:
    """to_dict() of the item-1 result carries every contract key with JSON-safe values."""
    r = four_hz_full_screen
    d = r.to_dict()
    assert CONTRACT_KEYS <= set(d)
    json.dumps(d)  # ints, floats, strings, lists and dicts only
    assert d["verdict"] == "FAIL"
    assert d["generalMaxRatePerSec"] == 4
    assert d["redMaxRatePerSec"] == 0
    assert d["frameCount"] == synth.frame_count(DURATION_S) == 90
    assert d["fps"] == synth.FPS
    assert d["windowPx"] == {"width": 341, "height": 256}
    assert d["grid"] == {"w": GRID[1], "h": GRID[0]}
    assert d["redRule"] == "wcag22"
    assert isinstance(d["engineVersion"], str) and d["engineVersion"]
    assert d["qualifyingGeneralFrames"] == list(r.qualifying_general)
    assert d["qualifyingRedFrames"] == []
    assert d["advisory"] == []  # 1.5 s is shorter than the 5 s an advisory needs
    assert len(d["violations"]) == 1
    assert set(d["violations"][0]) == {"start", "end", "type", "maxRatePerSec", "window", "frameIndices"}
    assert set(d["violations"][0]["window"]) == {"x", "y", "w", "h"}
    assert d["timeline"] and all(set(b) == {"t", "generalFlashes", "redFlashes"} for b in d["timeline"])
    assert set(d["heatmap"]) == {"cellSize", "w", "h", "data"}
    assert d["heatmap"]["cellSize"] == synth.CELL_PX
    assert (d["heatmap"]["w"], d["heatmap"]["h"]) == (GRID[1], GRID[0])


def test_heatmap_base64_decodes_to_a_uint16_grid_of_flash_counts(four_hz_full_screen: AnalysisResult) -> None:
    """heatmap.data is little-endian uint16, row-major, h rows by w columns; the whole screen flashed."""
    r = four_hz_full_screen
    h = r.to_dict()["heatmap"]
    decoded = np.frombuffer(base64.b64decode(h["data"]), dtype="<u2").reshape(h["h"], h["w"])
    assert decoded.shape == GRID
    assert tuple(r.grid) == GRID
    assert np.array_equal(decoded, r.heatmap)
    assert r.heatmap.dtype == np.uint16
    assert (decoded > 0).all()      # every cell took part in the full-screen flashing
    assert int(decoded.min()) >= 4  # at least four completed flashes per cell in 1.5 s at 4 Hz


def test_evidence_frame_indices_are_sorted_unique_and_in_range(four_hz_full_screen: AnalysisResult) -> None:
    """evidence_frame_indices() is deterministic, starts at frame 0, is sorted, unique, in range and capped."""
    r = four_hz_full_screen
    idx = evidence_frame_indices(r, max_frames=6)
    assert idx == sorted(set(idx))
    assert all(isinstance(i, int) for i in idx)
    assert all(0 <= i < r.frame_count for i in idx)
    assert 1 <= len(idx) <= 6
    assert idx[0] == 0
    assert evidence_frame_indices(r, max_frames=6) == idx
    assert len(evidence_frame_indices(r, max_frames=2)) <= 2


def test_evidence_frames_show_the_two_states_of_the_flash(four_hz_full_screen: AnalysisResult) -> None:
    """ADR-019: the first event frame is the completing frame, so it and the frame before it differ."""
    r = four_hz_full_screen
    first_event = r.violations[0].frame_indices[0]
    idx = evidence_frame_indices(r)
    assert first_event - 1 in idx and first_event in idx
    wave = square_wave(FOUR_HZ, BLACK, WHITE)
    before = wave((first_event - 1) / synth.FPS)
    after = wave(first_event / synth.FPS)
    assert before != after                      # the stills show a change, not the same state twice
    assert int(r.frame_stats.general_cells[first_event]) == GRID[0] * GRID[1]  # every cell completed here


def test_evidence_frame_indices_for_a_pass_is_frame_zero(static_page: AnalysisResult) -> None:
    """With no violations the only evidence frame is frame 0."""
    assert evidence_frame_indices(static_page) == [0]
