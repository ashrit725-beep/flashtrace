"""Acceptance tests for docs/SPEC.md section 5.10 (items 1-10), plus the input-validation and
report-shape checks of the engine contract.

Black box: only ``engine.analyze`` and the result object it returns are used.  Frames come
from ``engine/tests/synth.py`` and are streamed, never stacked.  A PASS here means "no 2.3.1
violation detected under the tested conditions", nothing more.

Counting convention (SPEC 5.5, ADR-005): a flash is counted when its SECOND transition
completes.  A 4 Hz square wave at 60 fps therefore completes one flash every 15 frames, so
any window of 60 frames holds 4 of them; a 3 Hz wave completes one every 20 frames and a
window holds 3.  The tests assert on verdicts, maximum rates, counts and whole-array equality,
never on the exact frame index at which a flash is recorded.
"""
from __future__ import annotations

import base64
import json
from typing import Sequence

import numpy as np
import pytest

from engine import analyze
from engine.analyze import evidence_frame_indices
from engine.tests import synth
from engine.tests.synth import (
    BLACK,
    DARK_RED,
    GREY_124,
    GREY_127,
    GREY_147,
    GREY_235,
    RED,
    WHITE,
    Region,
    full_screen,
    square_wave,
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
def run(regions: Sequence[Region], *, background=BLACK, duration_s: float = DURATION_S, **kwargs):
    """Analyse a scene through the public entry point, streaming the frames."""
    frames = synth.scene_frames(background, regions, duration_s)
    return analyze(frames, fps=synth.FPS, viewport=VIEWPORT, **kwargs)


def flashing_region(x: int, y: int, w: int, h: int, hz: float = FOUR_HZ,
                    colour_a=BLACK, colour_b=WHITE) -> Region:
    """A rectangle toggling between two colours, black <-> white at 4 Hz by default."""
    return Region(x, y, w, h, square_wave(hz, colour_a, colour_b))


def violation_types(result) -> list[str]:
    return [v.type for v in result.violations]


def violation_summary(result) -> list[tuple]:
    """Everything about a violation except its window position (used for invariance checks)."""
    return [(v.start, v.end, v.type, v.max_rate_per_sec, list(v.frame_indices)) for v in result.violations]


# Two scenes are shared by several tests; analyse each once per module.
@pytest.fixture(scope="module")
def four_hz_full_screen():
    return run([full_screen(square_wave(FOUR_HZ, BLACK, WHITE))])


@pytest.fixture(scope="module")
def region_26pct_at_origin():
    # 236 x 96 px = 59 x 24 cells = 1416 cells = 26.03% of a 5440-cell window.
    return run([flashing_region(0, 0, 236, 96)])


@pytest.fixture(scope="module")
def static_page():
    # Nothing changes, so nothing can flash.  0.5 s (30 frames) is shorter than one rate window.
    return run([full_screen(synth.constant(GREY_127))], duration_s=0.5)


# ---------------------------------------------------------------------------------------------
# Fixture self-check: the colour arithmetic documented in synth.py must be true.
# ---------------------------------------------------------------------------------------------
def test_fixture_colours_match_their_documented_luminance():
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
def test_01_four_hz_full_screen_black_white_fails(four_hz_full_screen):
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


def test_02_three_hz_full_screen_passes():
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


def test_03_region_covering_20_percent_of_a_window_passes():
    """SPEC 5.10 item 3 (first half): 4 Hz square wave covering 20% of one window => PASS."""
    region = flashing_region(0, 0, 136, 128)  # 34 x 32 cells
    assert region.cells == 1088 and region.cells / WINDOW_CELLS == pytest.approx(0.20)
    r = run([region])
    assert r.verdict == "PASS"
    assert r.general_max_rate == 0           # the flashes are real but never qualify by area
    assert r.qualifying_general == []
    assert r.violations == []
    assert r.frame_stats.general_area_max.max() == pytest.approx(1088 / WINDOW_CELLS)


def test_03_region_covering_26_percent_of_a_window_fails(region_26pct_at_origin):
    """SPEC 5.10 item 3 (second half): 4 Hz square wave covering 26% of one window => FAIL."""
    r = region_26pct_at_origin
    assert r.verdict == "FAIL"
    assert r.general_max_rate == 4
    assert violation_types(r) == ["general"]
    assert r.frame_stats.general_area_max.max() == pytest.approx(1416 / WINDOW_CELLS)


def test_03_region_covering_exactly_25_percent_passes():
    """SPEC 5.10 item 3 boundary: "no more than ... 25%" means exactly 25% of a window still passes."""
    region = flashing_region(0, 0, 340, 64)  # 85 x 16 cells: the full window width, a quarter of its height
    assert region.cells == 1360 and 1360 / WINDOW_CELLS == 0.25
    r = run([region])
    assert r.verdict == "PASS"
    assert r.general_max_rate == 0
    assert r.qualifying_general == []
    assert r.frame_stats.general_area_max.max() == pytest.approx(0.25)


def test_04_luminance_step_of_0_09_passes():
    """SPEC 5.10 item 4: Delta L = 0.09 => PASS (no transition, "10% or more" is required)."""
    r = run([full_screen(square_wave(FOUR_HZ, GREY_124, GREY_147))])
    assert r.verdict == "PASS"
    assert r.general_max_rate == 0
    assert r.red_max_rate == 0
    assert r.violations == []
    assert not r.heatmap.any()  # no cell completed a flash of either type


def test_05_both_states_above_0_80_pass():
    """SPEC 5.10 item 5: both states above L 0.80 with Delta L >= 0.10 => PASS (darker image not "below 0.80")."""
    r = run([full_screen(square_wave(FOUR_HZ, GREY_235, WHITE))])
    assert r.verdict == "PASS"
    assert r.general_max_rate == 0
    assert r.red_max_rate == 0
    assert r.violations == []
    assert not r.heatmap.any()


def test_06_saturated_red_against_equal_luminance_grey_fails_by_red_rule_only():
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


def test_07_split_region_inside_one_window_fails():
    """SPEC 5.10 item 7: a region split across two positions that exceeds 25% only when one window contains both parts => FAIL."""
    gap_px = 60
    assert HALF_W + gap_px + HALF_W <= 341   # 292 px: a single 341 px wide window spans both halves
    r = run(two_halves(gap_px))
    assert r.verdict == "FAIL"
    assert r.general_max_rate == 4
    assert violation_types(r) == ["general"]
    assert r.frame_stats.general_area_max.max() == pytest.approx(2 * HALF_CELLS / WINDOW_CELLS)


def test_07_split_region_in_separate_windows_passes():
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


def test_07_each_half_alone_passes():
    """SPEC 5.10 item 7: each half alone (14.9% of a window) => PASS."""
    r = run([flashing_region(0, 128, HALF_W, HALF_H)])
    assert r.verdict == "PASS"
    assert r.general_max_rate == 0
    assert r.violations == []


def test_08_translation_invariance(region_26pct_at_origin):
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


def test_09_determinism_same_input_twice_gives_identical_output():
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


def test_10_red_to_dark_red_is_a_general_flash_only_under_wcag22():
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


def test_10_red_to_dark_red_counts_as_red_under_the_legacy_rule():
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
# Contract extras: a static page, input validation, report shape, heatmap encoding, evidence frames
# ---------------------------------------------------------------------------------------------
def test_static_page_passes_with_nothing_flashing(static_page):
    """A page that never changes has no transitions, no flashes and a PASS verdict."""
    r = static_page
    assert r.verdict == "PASS"
    assert r.general_max_rate == 0 and r.red_max_rate == 0
    assert r.violations == [] and r.advisories == []
    assert not r.heatmap.any()
    assert r.frame_count == synth.frame_count(0.5)


def test_analyze_rejects_a_single_frame_array():
    """analyze() needs at least two frames (SPEC 5.1: T >= 2) and says so with ValueError."""
    with pytest.raises(ValueError):
        analyze(np.zeros((1, synth.VIEWPORT_H, synth.VIEWPORT_W, 3), dtype=np.uint8), fps=synth.FPS)


def test_analyze_rejects_a_single_frame_iterable():
    """The same two-frame minimum applies when frames arrive as a generator."""
    one_frame = synth.scene_frames(BLACK, [], duration_s=1 / synth.FPS)
    with pytest.raises(ValueError):
        analyze(one_frame, fps=synth.FPS)


def test_analyze_rejects_float_frames():
    """Frames must be uint8 sRGB; a float array is refused rather than silently rescaled."""
    with pytest.raises(ValueError):
        analyze(np.zeros((2, synth.VIEWPORT_H, synth.VIEWPORT_W, 3), dtype=np.float32), fps=synth.FPS)


def test_analyze_rejects_an_unknown_red_rule():
    """Only "wcag22" and "wcag21-legacy" exist (ADR-001)."""
    with pytest.raises(ValueError):
        analyze(np.zeros((2, synth.VIEWPORT_H, synth.VIEWPORT_W, 3), dtype=np.uint8),
                fps=synth.FPS, red_rule="wcag20")


def test_to_dict_has_the_contract_keys_and_is_json_safe(four_hz_full_screen):
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


def test_heatmap_base64_decodes_to_a_uint16_grid_of_flash_counts(four_hz_full_screen):
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


def test_evidence_frame_indices_are_sorted_unique_and_in_range(four_hz_full_screen):
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


def test_evidence_frame_indices_for_a_pass_is_frame_zero(static_page):
    """With no violations the only evidence frame is frame 0."""
    assert evidence_frame_indices(static_page) == [0]
