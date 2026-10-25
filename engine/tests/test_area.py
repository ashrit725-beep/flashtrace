"""Black-box unit tests for engine/area.py and the window sizes in engine/cells.py
(SPEC T4, SPEC 5.6, ADR-006, ADR-007, ADR-019).

The window scoring is checked against a deliberately naive brute force
(nested loops over every window position) so the integral-image shortcut is
proven equal to the obvious definition.  The qualifying-event tests use the
real 1024x768 grid (256x192 cells) and the real 341x256 px window (85x64
cells = 5440 cells, so 25% is exactly 1360 cells).

Event rule under test (SPEC 5.6, ADR-019): E[t] = M[t-1] | M[t] is scored for
area_max; an event counted at frame t claims the completions of frames t and
t+1, and a later frame is scored again using only the completions no earlier
event has claimed.
"""
from __future__ import annotations

import math

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from engine.area import (
    AreaResult,
    best_window,
    count_events,
    integral_image,
    qualifying_events,
    unclaimed_share,
    union_with_previous_frames,
    window_sums,
)
from engine.cells import window_cells, window_cells_full, window_px
from engine.constants import EVENT_FRAME_TOLERANCE, MAX_AREA_FRACTION

_PROPERTY = settings(deadline=None, derandomize=True, max_examples=40, database=None)

# SPEC section 4 derived numbers: 1024x768 px -> 256x192 cells; 341x256 px -> 85x64 cells.
CY, CX = 192, 256
WY, WX = 64, 85
WINDOW_CELLS = WY * WX  # 5440
QUARTER = WINDOW_CELLS // 4  # 1360 cells is exactly 25% of one window


def _brute_window_sums(mask: np.ndarray, wy: int, wx: int) -> np.ndarray:
    """Naive count of True cells in every (wy x wx) window position."""
    cy, cx = mask.shape
    out = np.zeros((cy - wy + 1, cx - wx + 1), dtype=np.int64)
    for y in range(cy - wy + 1):
        for x in range(cx - wx + 1):
            out[y, x] = int(mask[y : y + wy, x : x + wx].sum())
    return out


def _brute_best(mask: np.ndarray, wy: int, wx: int) -> tuple[int, int, int]:
    """Naive best window: first strictly-greater count wins, scanning rows then columns."""
    sums = _brute_window_sums(mask, wy, wx)
    best = (-1, 0, 0)
    for y in range(sums.shape[0]):
        for x in range(sums.shape[1]):
            if int(sums[y, x]) > best[0]:
                best = (int(sums[y, x]), y, x)
    return best


def _region(n_cells: int, y0: int = 0, x0: int = 0, width: int = 34) -> np.ndarray:
    """A CY x CX mask with n_cells filled row-major inside a `width`-wide strip starting at (y0, x0).

    34 columns keep up to 42 rows (1428 cells) inside one 64 x 85 window, so the
    whole region is always visible to a single window position.
    """
    mask = np.zeros((CY, CX), dtype=bool)
    rows, rest = divmod(n_cells, width)
    mask[y0 : y0 + rows, x0 : x0 + width] = True
    mask[y0 + rows, x0 : x0 + rest] = True
    return mask


def _frames(n_frames: int) -> np.ndarray:
    """An all-False mask stack of shape [T, CY, CX]."""
    return np.zeros((n_frames, CY, CX), dtype=bool)


# ---------------------------------------------------------------------------
# integral_image / window_sums / best_window
# ---------------------------------------------------------------------------


def test_integral_image_shape_dtype_and_zero_border() -> None:
    """SPEC 5.6 step 2: the integral image carries one extra zero row and column (int64)."""
    rng = np.random.default_rng(1)
    mask = rng.random((7, 9)) < 0.5
    ii = integral_image(mask)
    assert ii.shape == (8, 10)
    assert ii.dtype == np.int64
    assert np.all(ii[0, :] == 0)
    assert np.all(ii[:, 0] == 0)
    assert ii[-1, -1] == mask.sum()


def test_integral_image_equals_prefix_sums() -> None:
    """SPEC 5.6 step 2: ii[y, x] is the number of True cells in mask[:y, :x]."""
    rng = np.random.default_rng(2)
    mask = rng.random((7, 9)) < 0.5
    ii = integral_image(mask)
    for y in range(8):
        for x in range(10):
            assert ii[y, x] == mask[:y, :x].sum()


_SIZES = [
    # (cy, cx, wy, wx, seed) - includes windows equal to the grid in one or both directions.
    (6, 8, 3, 4, 0),
    (10, 7, 10, 3, 1),
    (5, 9, 2, 9, 2),
    (7, 7, 7, 7, 3),
    (12, 16, 4, 6, 4),
    (1, 1, 1, 1, 5),
    (3, 11, 1, 1, 6),
]


@pytest.mark.parametrize("cy,cx,wy,wx,seed", _SIZES)
def test_window_sums_matches_brute_force(cy: int, cx: int, wy: int, wx: int, seed: int) -> None:
    """SPEC 5.6 step 2: every window position's count equals the naive sum."""
    mask = np.random.default_rng(seed).random((cy, cx)) < 0.5
    sums = window_sums(integral_image(mask), wy, wx)
    assert sums.shape == (cy - wy + 1, cx - wx + 1)
    assert sums.dtype == np.int64
    np.testing.assert_array_equal(sums, _brute_window_sums(mask, wy, wx))


@pytest.mark.parametrize("cy,cx,wy,wx,seed", _SIZES)
def test_best_window_matches_brute_force(cy: int, cx: int, wy: int, wx: int, seed: int) -> None:
    """SPEC 5.6 step 2: best_window returns the maximum count and its first (row-major) position."""
    mask = np.random.default_rng(seed).random((cy, cx)) < 0.5
    count, y, x = best_window(mask, wy, wx)
    assert (int(count), int(y), int(x)) == _brute_best(mask, wy, wx)


def test_best_window_with_window_equal_to_grid() -> None:
    """SPEC 5.6: a window as large as the grid has one position (0, 0) and counts everything."""
    mask = np.random.default_rng(9).random((6, 8)) < 0.5
    count, y, x = best_window(mask, 6, 8)
    assert (int(count), int(y), int(x)) == (int(mask.sum()), 0, 0)


def test_best_window_tie_prefers_smallest_y() -> None:
    """SPEC 5.6: 'ties -> smallest y, then smallest x' - (3,0) and (0,4) tie, (0,4) wins."""
    mask = np.zeros((6, 6), dtype=bool)
    mask[3, 0] = True
    mask[0, 4] = True
    count, y, x = best_window(mask, 1, 1)
    assert (int(count), int(y), int(x)) == (1, 0, 4)


def test_best_window_tie_prefers_smallest_x_within_a_row() -> None:
    """SPEC 5.6: same row, so the tie goes to the smaller x - (2,1) beats (2,4)."""
    mask = np.zeros((6, 6), dtype=bool)
    mask[2, 4] = True
    mask[2, 1] = True
    count, y, x = best_window(mask, 1, 1)
    assert (int(count), int(y), int(x)) == (1, 2, 1)


def test_best_window_tie_with_2x2_window() -> None:
    """SPEC 5.6: cells (1,1) and (3,3) each fill one 2x2 window; the first position (0,0) wins."""
    mask = np.zeros((6, 6), dtype=bool)
    mask[1, 1] = True
    mask[3, 3] = True
    count, y, x = best_window(mask, 2, 2)
    assert (int(count), int(y), int(x)) == (1, 0, 0)


def test_best_window_of_empty_mask_is_zero_at_origin() -> None:
    """SPEC 5.6: nothing flashing -> count 0 and, by the tie rule, position (0, 0)."""
    count, y, x = best_window(np.zeros((5, 7), dtype=bool), 2, 3)
    assert (int(count), int(y), int(x)) == (0, 0, 0)


def test_best_window_of_full_mask() -> None:
    """SPEC 5.6: a fully flashing grid gives count wy*wx at the first position."""
    count, y, x = best_window(np.ones((5, 7), dtype=bool), 2, 3)
    assert (int(count), int(y), int(x)) == (6, 0, 0)


@st.composite
def _mask_and_window(draw: st.DrawFn) -> tuple[np.ndarray, int, int]:
    """A small random mask plus a window that fits inside it."""
    cy = draw(st.integers(min_value=1, max_value=8))
    cx = draw(st.integers(min_value=1, max_value=8))
    wy = draw(st.integers(min_value=1, max_value=cy))
    wx = draw(st.integers(min_value=1, max_value=cx))
    bits = draw(st.lists(st.booleans(), min_size=cy * cx, max_size=cy * cx))
    return np.array(bits, dtype=bool).reshape(cy, cx), wy, wx


@_PROPERTY
@given(_mask_and_window())
def test_property_best_window_equals_brute_force(case: tuple[np.ndarray, int, int]) -> None:
    """SPEC 5.6 step 2: for any small mask and window the fast path equals the naive scan."""
    mask, wy, wx = case
    count, y, x = best_window(mask, wy, wx)
    assert (int(count), int(y), int(x)) == _brute_best(mask, wy, wx)
    np.testing.assert_array_equal(window_sums(integral_image(mask), wy, wx),
                                  _brute_window_sums(mask, wy, wx))


# ---------------------------------------------------------------------------
# qualifying_events (SPEC 5.6 steps 1-4)
# ---------------------------------------------------------------------------


def test_constants_used_by_this_module() -> None:
    """SPEC T4 / ADR-019: the area limit is 25% and the event tolerance is one frame."""
    assert MAX_AREA_FRACTION == 0.25
    assert EVENT_FRAME_TOLERANCE == 1


def test_small_grid_hand_example() -> None:
    """SPEC T4 on a 4x4 grid with a 4x4 window: 4 cells (25%) do not qualify, 4 + 5 cells (56%) do."""
    masks = np.zeros((2, 4, 4), dtype=bool)
    masks[0, 0, :4] = True          # 4 of 16 cells = 0.25 exactly
    masks[1, 1, :4] = True
    masks[1, 2, 0] = True           # 5 of 16 cells = 0.3125
    res = qualifying_events(masks, 4, 4)
    # E[0] = M[0] = 4 cells (nothing before frame 0); E[1] = M[0] | M[1] = 9 cells.
    assert res.area_max.tolist() == [4 / 16, 9 / 16]
    assert res.qualifying_raw.tolist() == [False, True]
    assert res.qualifying.tolist() == [False, True]
    res_single = qualifying_events(masks[:1], 4, 4)
    assert res_single.area_max.tolist() == [4 / 16]
    assert res_single.qualifying_raw.tolist() == [False]


def test_exactly_25_percent_does_not_qualify() -> None:
    """SPEC T4: 'no more than ... 25%' passes, so 1360 of 5440 cells (exactly 0.25) is not qualifying."""
    masks = _frames(3)
    masks[1] = _region(QUARTER)
    res = qualifying_events(masks, WY, WX)
    assert isinstance(res, AreaResult)
    assert res.area_max[0] == 0.0
    assert res.area_max[1] == 0.25
    assert res.area_max[2] == 0.25  # E[2] = M[1] | M[2] still sees the same 1360 cells
    assert not res.qualifying_raw.any()
    assert not res.qualifying.any()


def test_one_cell_over_25_percent_qualifies() -> None:
    """ADR-006: 'strict > 25%' - 1361 cells (0.25018 of the window) qualify, counted once at their frame."""
    masks = _frames(3)
    masks[1] = _region(QUARTER + 1)
    res = qualifying_events(masks, WY, WX)
    assert res.area_max[1] == pytest.approx((QUARTER + 1) / WINDOW_CELLS, abs=1e-12)
    assert res.qualifying_raw.tolist() == [False, True, True]
    assert res.qualifying.tolist() == [False, True, False]


def test_two_frame_union_merges_a_region_split_across_adjacent_frames() -> None:
    """SPEC 5.6 step 1: E[t] = M[t-1] | M[t] - 700 cells at t plus 700 at t+1 qualify at t+1 only."""
    masks = _frames(5)
    masks[2] = _region(700, 0, 0)
    masks[3] = _region(700, 0, 40)
    res = qualifying_events(masks, WY, WX)
    assert res.area_max[3] == pytest.approx(1400 / WINDOW_CELLS, abs=1e-12)
    assert res.area_max[2] == pytest.approx(700 / WINDOW_CELLS, abs=1e-12)  # E[2] = M[2]
    assert res.area_max[4] == pytest.approx(700 / WINDOW_CELLS, abs=1e-12)  # E[4] = M[3]
    assert res.area_max[0] == 0.0
    assert res.area_max[1] == 0.0
    assert res.qualifying_raw.tolist() == [False, False, False, True, False]
    assert res.qualifying.tolist() == [False, False, False, True, False]
    assert (int(res.window_y[3]), int(res.window_x[3])) == (0, 0)


def test_each_half_alone_does_not_qualify() -> None:
    """SPEC 5.10 #7: 'each half alone => PASS' - 700 cells never exceed 25% of a window."""
    masks = _frames(5)
    masks[2] = _region(700, 0, 0)
    res = qualifying_events(masks, WY, WX)
    assert res.area_max.max() == pytest.approx(700 / WINDOW_CELLS, abs=1e-12)
    assert not res.qualifying_raw.any()


def test_single_completion_is_exactly_one_event_at_its_frame() -> None:
    """SPEC 5.6 / ADR-019: one flash completing at frame t qualifies on t and t+1 and is ONE event, at t."""
    masks = _frames(8)
    masks[3] = _region(1400)
    res = qualifying_events(masks, WY, WX)
    assert np.flatnonzero(res.qualifying_raw).tolist() == [3, 4]
    assert np.flatnonzero(res.qualifying).tolist() == [3]


def test_completions_two_frames_apart_are_one_event_each() -> None:
    """ADR-019: completions at 3, 5, 7, 9 keep every frame 3..10 qualifying, and each completion is an event."""
    masks = _frames(12)
    for t in (3, 5, 7, 9):
        masks[t] = _region(1400)
    res = qualifying_events(masks, WY, WX)
    assert np.flatnonzero(res.qualifying_raw).tolist() == list(range(3, 11))
    assert np.flatnonzero(res.qualifying).tolist() == [3, 5, 7, 9]


def test_same_cells_completing_on_frames_0_1_2_give_events_at_0_and_2() -> None:
    """SPEC 5.6 step 4: the event at 0 claims frames 0 and 1; frame 2's completions are unclaimed, so they
    are a second event.  Frame 3 sees only claimed frame 2 and empty frame 3."""
    masks = _frames(5)
    masks[0:3] = _region(1400)
    res = qualifying_events(masks, WY, WX)
    assert res.qualifying_raw.tolist() == [True, True, True, True, False]
    assert res.qualifying.tolist() == [True, False, True, False, False]


@pytest.mark.parametrize("n_frames", [1, 2, 3, 4, 5, 8, 59])
def test_count_events_for_the_same_cells_completing_on_consecutive_frames(n_frames: int) -> None:
    """SPEC 5.6 step 4: each event claims two frames, so the same region completing on n frames in a row
    from frame 4 is counted at 4, 6, 8, ... - ceil(n/2) events."""
    masks = _frames(70)
    masks[4 : 4 + n_frames] = _region(1400)
    events = qualifying_events(masks, WY, WX).qualifying
    assert np.flatnonzero(events).tolist() == list(range(4, 4 + n_frames, 2))
    assert int(events.sum()) == math.ceil(n_frames / 2)


def test_count_events_with_zero_tolerance_counts_every_qualifying_frame() -> None:
    """SPEC 5.6 parameter: with tolerance 0 an event claims only its own frame, so every frame whose own
    completions qualify is an event."""
    masks = _frames(6)
    for t in (1, 2, 3, 5):
        masks[t] = _region(1400)
    raw = np.array([False, True, True, True, False, True])
    events = count_events(masks, raw, 0, (WY, WX), WINDOW_CELLS)
    assert events.tolist() == raw.tolist()


def test_one_flash_completing_over_two_neighbouring_frames_is_one_event() -> None:
    """ADR-019 as amended: a smooth flash whose cells reach their extreme byte one frame apart completes
    1400 cells at frame 3 and 1400 other cells at frame 4.  Frames 3, 4 and 5 all exceed 25%, but it is
    one flash: counted once, at 3 (the old ceil(n/2) rule counted it twice, at 3 and 5)."""
    masks = _frames(8)
    masks[3] = _region(1400, 0, 0)
    masks[4] = _region(1400, 0, 40)
    res = qualifying_events(masks, WY, WX)
    assert np.flatnonzero(res.qualifying_raw).tolist() == [3, 4, 5]
    assert np.flatnonzero(res.qualifying).tolist() == [3]


def test_claimed_completions_are_not_scored_again() -> None:
    """SPEC 5.6 step 4: frame 5 sees frame 4 (claimed by the event at 3) and 700 new cells; only the 700
    count, which is not enough.  Frame 6 adds 700 more unclaimed cells: 1400, a new event."""
    masks = _frames(9)
    masks[3] = _region(1400, 0, 0)
    masks[4] = _region(1400, 0, 40)
    masks[5] = _region(700, 0, 80)
    masks[6] = _region(700, 0, 120)
    res = qualifying_events(masks, WY, WX)
    assert bool(res.qualifying_raw[5])  # all of E[5] = M[4] | M[5] is 2100 cells
    assert np.flatnonzero(res.qualifying).tolist() == [3, 6]


def test_two_regions_qualifying_on_consecutive_frames_count_once() -> None:
    """ADR-019 as amended, the recorded trade-off: two separate regions, each over 25% on its own, that
    complete one frame apart are counted as one event, as under ADR-006."""
    masks = _frames(6)
    masks[2] = _region(1400, 0, 0)
    masks[3] = _region(1400, 100, 150)
    res = qualifying_events(masks, WY, WX)
    assert np.flatnonzero(res.qualifying).tolist() == [2]


def test_unclaimed_share_skips_claimed_frames() -> None:
    """SPEC 5.6 step 4: the share at frame 4 counts frame 3's cells only while frame 3 is unclaimed."""
    masks = _frames(5)
    masks[3] = _region(700, 0, 0)
    masks[4] = _region(700, 0, 40)
    free = np.zeros(5, dtype=bool)
    claimed = free.copy()
    claimed[3] = True
    assert unclaimed_share(masks, 4, free, 1, (WY, WX), WINDOW_CELLS) == 1400 / WINDOW_CELLS
    assert unclaimed_share(masks, 4, claimed, 1, (WY, WX), WINDOW_CELLS) == 700 / WINDOW_CELLS


def test_union_looks_backwards_only() -> None:
    """SPEC 5.6 step 1: E[t] = M[t-1] | M[t]; frame 0 has nothing before it (M[-1] = empty)."""
    masks = np.zeros((4, 1, 3), dtype=bool)
    masks[0, 0, 0] = True
    masks[2, 0, 2] = True
    events = union_with_previous_frames(masks, 1)
    assert events[:, 0, :].tolist() == [
        [True, False, False],   # E[0] = M[0]
        [True, False, False],   # E[1] = M[0] | M[1]
        [False, False, True],   # E[2] = M[1] | M[2]
        [False, False, True],   # E[3] = M[2] | M[3]
    ]


def test_two_events_15_frames_apart_count_twice() -> None:
    """ADR-019: separate events are each counted at their own completing frame."""
    masks = _frames(30)
    masks[10] = _region(1400)
    masks[25] = _region(1400)
    res = qualifying_events(masks, WY, WX)
    assert np.flatnonzero(res.qualifying_raw).tolist() == [10, 11, 25, 26]
    assert np.flatnonzero(res.qualifying).tolist() == [10, 25]
    assert int(res.qualifying.sum()) == 2


def test_last_frame_event_is_counted_at_the_last_frame() -> None:
    """SPEC 5.6: an event completing in the last frame has a 1-frame run and is still counted there."""
    masks = _frames(4)
    masks[3] = _region(1400)
    res = qualifying_events(masks, WY, WX)
    assert res.area_max[3] == pytest.approx(1400 / WINDOW_CELLS, abs=1e-12)
    assert res.qualifying_raw.tolist() == [False, False, False, True]
    assert res.qualifying.tolist() == [False, False, False, True]


def test_single_frame_input_is_scored() -> None:
    """SPEC 5.6: with T = 1 there is no frame before it (M[-1] = empty); the frame still gets scored."""
    masks = _frames(1)
    masks[0] = _region(1400)
    res = qualifying_events(masks, WY, WX)
    assert res.area_max.tolist() == [pytest.approx(1400 / WINDOW_CELLS, abs=1e-12)]
    assert res.qualifying_raw.tolist() == [True]
    assert res.qualifying.tolist() == [True]


def test_empty_masks_give_zero_everything_with_the_right_dtypes() -> None:
    """SPEC 5.8: per-frame arrays have length T; nothing flashing means zeros and (0, 0) windows."""
    res = qualifying_events(_frames(3), WY, WX)
    for name in ("area_max", "window_y", "window_x", "qualifying_raw", "qualifying"):
        assert getattr(res, name).shape == (3,), name
    assert res.area_max.dtype == np.float64
    assert res.window_y.dtype == np.int64
    assert res.window_x.dtype == np.int64
    assert res.qualifying_raw.dtype == np.bool_
    assert res.qualifying.dtype == np.bool_
    assert np.all(res.area_max == 0.0)
    assert np.all(res.window_y == 0)
    assert np.all(res.window_x == 0)
    assert not res.qualifying_raw.any()
    assert not res.qualifying.any()


def test_translation_invariance_and_window_position() -> None:
    """SPEC 5.10 #8 / 5.6: the same region elsewhere gives the same area; the window follows it."""
    at_origin = _region(1400, 0, 0)
    moved = _region(1400, 100, 150)
    res_a = qualifying_events(at_origin[None], WY, WX)
    res_b = qualifying_events(moved[None], WY, WX)
    assert res_a.area_max[0] == res_b.area_max[0]
    assert bool(res_a.qualifying[0]) and bool(res_b.qualifying[0])
    assert (int(res_a.window_y[0]), int(res_a.window_x[0])) == (0, 0)
    wy0, wx0 = int(res_b.window_y[0]), int(res_b.window_x[0])
    # The reported window must hold the whole region ...
    assert int(moved[wy0 : wy0 + WY, wx0 : wx0 + WX].sum()) == 1400
    # ... and among all such windows the tie rule picks the smallest y, then the smallest x.
    ys, xs = np.nonzero(moved)
    assert (wy0, wx0) == (int(ys.max()) - WY + 1, int(xs.max()) - WX + 1)


def test_region_in_the_bottom_right_corner_is_found() -> None:
    """SPEC T4: 'anywhere on the displayed screen area' - the window reaches the last cells."""
    masks = _frames(1)
    masks[0] = _region(1400, CY - 42, CX - 34)
    res = qualifying_events(masks, WY, WX)
    assert bool(res.qualifying[0])
    assert (int(res.window_y[0]), int(res.window_x[0])) == (CY - WY, CX - WX)


def test_tolerance_frames_zero_disables_the_union() -> None:
    """SPEC 5.6 step 1 parameter: with tolerance_frames=0, E[t] = M[t], so the split region does not qualify."""
    masks = _frames(5)
    masks[2] = _region(700, 0, 0)
    masks[3] = _region(700, 0, 40)
    res = qualifying_events(masks, WY, WX, tolerance_frames=0)
    assert res.area_max[2] == pytest.approx(700 / WINDOW_CELLS, abs=1e-12)
    assert res.area_max[3] == pytest.approx(700 / WINDOW_CELLS, abs=1e-12)
    assert not res.qualifying_raw.any()


def test_max_fraction_parameter_is_respected() -> None:
    """SPEC T4 parameter: lowering max_fraction to 0.2 makes the exactly-25% region qualify."""
    masks = _frames(3)
    masks[1] = _region(QUARTER)
    res = qualifying_events(masks, WY, WX, max_fraction=0.2)
    assert res.qualifying_raw.tolist() == [False, True, True]
    assert res.qualifying.tolist() == [False, True, False]


# ---------------------------------------------------------------------------
# Full-window denominator and window sizes (SPEC 5.6, ADR-007, ADR-019)
# ---------------------------------------------------------------------------


def test_clipped_window_still_divides_by_the_full_window() -> None:
    """ADR-019: on a 25-row grid the scan uses 25x85 cells but 600 cells are 600/5440 of the field, not 600/2125."""
    masks = np.zeros((2, 25, 256), dtype=bool)
    masks[0, :, :24] = True  # 25 x 24 = 600 cells
    res = qualifying_events(masks, 25, 85, full_window_cells=WINDOW_CELLS)
    assert res.area_max[0] == 600 / WINDOW_CELLS
    assert not res.qualifying_raw.any()
    clipped = qualifying_events(masks, 25, 85)  # the old, clipped denominator would call it 28%
    assert clipped.area_max[0] == 600 / (25 * 85)
    assert bool(clipped.qualifying_raw[0])


def test_full_window_smaller_than_the_scan_is_rejected() -> None:
    """A denominator below the scanned window could push the fraction above 1, so it is refused."""
    with pytest.raises(ValueError):
        qualifying_events(_frames(1), WY, WX, full_window_cells=WINDOW_CELLS - 1)


def test_window_sizes_at_the_reference_viewport() -> None:
    """SPEC section 4: 341x256 px at 1024 wide is 85x64 cells (341/4 = 85.25 -> 85, 256/4 = 64)."""
    assert window_px(1024) == (341, 256)
    assert window_cells_full(1024) == (WY, WX)
    assert window_cells(1024, CY, CX) == (WY, WX)


def test_short_viewport_clamps_the_scan_but_not_the_full_window() -> None:
    """ADR-019: a 1024x100 viewport has 25 cell rows; the scan is 25x85, the full window stays 64x85."""
    assert window_cells(1024, 25, 256) == (25, 85)
    assert window_cells_full(1024) == (64, 85)


def test_window_sizes_round_halves_up() -> None:
    """SPEC 5.6: 'rounding halves up' - at 1032 px wide 256*1032/1024/4 = 64.5 -> 65 (round() would give 64)."""
    assert 256 * 1032 / 1024 / 4 == 64.5
    assert window_cells_full(1032)[0] == 65
    # 256 * 1026 / 1024 = 256.5 px -> 257 px.
    assert window_px(1026)[1] == 257
