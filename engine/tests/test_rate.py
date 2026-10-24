"""Black-box unit tests for engine/rate.py (SPEC T5, T7, 5.7, 5.8, 5.9, ADR-009).

Event arrays are hand-made booleans ("a qualifying flash completed at this
frame").  Expected violations and advisories were traced by hand and cross-
checked with a naive loop before being written down; the property tests keep
that naive loop beside the fast implementation.
"""
from __future__ import annotations

import math

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from engine.constants import (
    ADVISORY_MIN_DURATION_SECONDS,
    ADVISORY_MIN_FLASHES_PER_SECOND,
    MAX_FLASHES_PER_SECOND,
    RATE_WINDOW_SECONDS,
)
from engine.rate import (
    find_advisories,
    find_violations,
    timeline,
    trailing_counts,
    window_counts,
)

_PROPERTY = settings(deadline=None, derandomize=True, max_examples=60, database=None)

FPS = 60  # SPEC 5.1 default animation frame rate


def _events(n_frames: int, frames: list[int]) -> np.ndarray:
    """A bool[T] event array that is True at the listed frames."""
    events = np.zeros(n_frames, dtype=bool)
    events[frames] = True
    return events


def _every(n_frames: int, step: int) -> np.ndarray:
    """Events at frames 0, step, 2*step, ... below n_frames."""
    events = np.zeros(n_frames, dtype=bool)
    events[::step] = True
    return events


def _brute_trailing(events: np.ndarray, fps: int) -> list[int]:
    """Naive count of events in [t-fps+1, t] for every t."""
    return [int(events[max(0, t - fps + 1) : t + 1].sum()) for t in range(len(events))]


def _brute_window_counts(events: np.ndarray, fps: int) -> list[int]:
    """Naive count per window start t0 in [0, T-fps]; one window when T < fps."""
    n_frames = len(events)
    if n_frames < fps:
        return [int(events.sum())]
    return [int(events[t0 : t0 + fps].sum()) for t0 in range(n_frames - fps + 1)]


def _violations(result: list) -> list[tuple[int, int, int]]:
    """Normalise violations to plain Python ints."""
    return [(int(start), int(end), int(count)) for start, end, count in result]


def _advisories(result: list) -> list[tuple[int, int, float]]:
    """Normalise advisories to plain Python numbers."""
    return [(int(start), int(end), float(rate)) for start, end, rate in result]


def _timeline(result: list) -> list[tuple[float, int, int]]:
    """Normalise timeline bins to plain Python numbers."""
    return [(float(t), int(general), int(red)) for t, general, red in result]


# ---------------------------------------------------------------------------
# Constants this module relies on
# ---------------------------------------------------------------------------


def test_rate_constants() -> None:
    """SPEC T5 / T7: more than 3 per one-second window fails; advisories need >= 2/s for > 5 s."""
    assert MAX_FLASHES_PER_SECOND == 3
    assert RATE_WINDOW_SECONDS == 1.0
    assert ADVISORY_MIN_FLASHES_PER_SECOND == 2
    assert ADVISORY_MIN_DURATION_SECONDS == 5.0


# ---------------------------------------------------------------------------
# trailing_counts / window_counts
# ---------------------------------------------------------------------------


def test_trailing_counts_hand_made() -> None:
    """SPEC 5.9: trailing[t] counts events in [t-fps+1, t] (fps=4, events at 0, 2, 3, 7)."""
    out = trailing_counts(_events(10, [0, 2, 3, 7]), 4)
    assert out.shape == (10,)
    assert out.dtype == np.int64
    assert out.tolist() == [1, 1, 2, 3, 2, 2, 1, 1, 1, 1]


def test_window_counts_hand_made() -> None:
    """SPEC 5.7: count(t0) over [t0, t0+fps-1] for t0 in [0, T-fps] (fps=4, events at 0, 2, 3, 7)."""
    out = window_counts(_events(10, [0, 2, 3, 7]), 4)
    assert out.shape == (7,)
    assert out.dtype == np.int64
    assert out.tolist() == [3, 2, 2, 1, 1, 1, 1]


def test_counts_when_capture_is_shorter_than_one_second() -> None:
    """SPEC 5.7: 'when T < fps the single window is the whole capture'."""
    events = np.array([True, False, True])
    assert window_counts(events, 4).tolist() == [2]
    assert trailing_counts(events, 4).tolist() == [1, 1, 2]


def test_counts_when_capture_is_exactly_one_second() -> None:
    """SPEC 5.7: T == fps gives exactly one window start, t0 = 0."""
    events = np.array([True, True, False, True])
    assert window_counts(events, 4).tolist() == [3]
    assert trailing_counts(events, 4).tolist() == [1, 2, 2, 3]


def test_counts_with_no_events_are_zero() -> None:
    """SPEC 5.7: no qualifying flashes means every count is 0."""
    events = np.zeros(100, dtype=bool)
    assert not trailing_counts(events, FPS).any()
    assert not window_counts(events, FPS).any()
    assert window_counts(events, FPS).shape == (41,)


@_PROPERTY
@given(
    st.integers(min_value=1, max_value=10),
    st.lists(st.booleans(), min_size=1, max_size=40),
)
def test_property_counts_match_naive_loops(fps: int, bits: list[bool]) -> None:
    """SPEC 5.7 / 5.9: for any events and fps, both counters equal the obvious definition."""
    events = np.array(bits, dtype=bool)
    assert trailing_counts(events, fps).tolist() == _brute_trailing(events, fps)
    assert window_counts(events, fps).tolist() == _brute_window_counts(events, fps)


# ---------------------------------------------------------------------------
# find_violations (SPEC T5, 5.7)
# ---------------------------------------------------------------------------


def test_four_flashes_per_second_is_one_violation() -> None:
    """SPEC T5: events every 15 frames at 60 fps put 4 in every window -> one violation, max 4."""
    result = _violations(find_violations(_every(120, 15), FPS))
    assert result == [(0, 119, 4)]


def test_three_flashes_per_second_is_not_a_violation() -> None:
    """SPEC T5: 'more than three' is exclusive - events every 20 frames (3 per window) pass."""
    assert _violations(find_violations(_every(120, 20), FPS)) == []


def test_two_separated_bursts_are_two_violations() -> None:
    """SPEC 5.7: each maximal run of failing window starts is its own violation, with its own peak."""
    events = _events(300, [10, 20, 30, 40, 200, 205, 210, 215, 220])
    result = _violations(find_violations(events, FPS))
    assert result == [(0, 69, 4), (156, 264, 5)]


def test_violation_end_is_clipped_to_the_last_frame() -> None:
    """SPEC 5.7: end = last t0 + fps - 1 'clipped to T-1' - a 30-frame capture ends at 29."""
    events = _events(30, [0, 5, 10, 15])
    assert _violations(find_violations(events, FPS)) == [(0, 29, 4)]


def test_max_per_second_parameter_lowers_the_bar() -> None:
    """SPEC T5 parameter: with max_per_second=2 the 3-per-window pattern becomes a violation."""
    result = _violations(find_violations(_every(120, 20), FPS, max_per_second=2))
    assert result == [(0, 119, 3)]


def test_violation_entries_are_integers() -> None:
    """Contract: (start_frame, end_frame, max_count) are integers, not floats or arrays."""
    (violation,) = find_violations(_every(120, 15), FPS)
    start, end, count = violation
    assert isinstance(int(start), int) and float(start) == int(start)
    assert isinstance(int(end), int) and float(end) == int(end)
    assert isinstance(int(count), int) and float(count) == int(count)


def test_no_events_means_no_violations() -> None:
    """SPEC 5.7: an empty event array can never exceed three per second."""
    assert find_violations(np.zeros(200, dtype=bool), FPS) == []


@_PROPERTY
@given(
    st.integers(min_value=1, max_value=8),
    st.lists(st.booleans(), min_size=1, max_size=40),
)
def test_property_violations_cover_exactly_the_failing_window_starts(fps: int, bits: list[bool]) -> None:
    """SPEC 5.7: violations are the maximal runs of t0 with count(t0) > 3, with the run's peak count."""
    events = np.array(bits, dtype=bool)
    counts = _brute_window_counts(events, fps)
    failing = {t0 for t0, c in enumerate(counts) if c > MAX_FLASHES_PER_SECOND}
    covered: set[int] = set()
    for start, end, peak in _violations(find_violations(events, fps)):
        last_t0 = max(0, end - fps + 1)
        assert start <= last_t0
        assert end == min(last_t0 + fps - 1, len(events) - 1)
        run = set(range(start, last_t0 + 1))
        assert run <= failing
        assert run.isdisjoint(covered)
        assert peak == max(counts[t0] for t0 in run)
        # Maximal: the run cannot be extended on either side.
        assert (start - 1) not in failing
        assert (last_t0 + 1) not in failing
        covered |= run
    assert covered == failing


# ---------------------------------------------------------------------------
# find_advisories (SPEC T7, 5.9, ADR-009)
# ---------------------------------------------------------------------------


def test_two_per_second_for_seven_seconds_is_one_advisory() -> None:
    """SPEC T7: events every 30 frames for 7 s keep trailing >= 2 from frame 30 to 419 (> 5 s)."""
    result = _advisories(find_advisories(_every(420, 30), FPS))
    assert len(result) == 1
    start, end, mean_rate = result[0]
    assert (start, end) == (30, 419)
    assert mean_rate == pytest.approx(2.0, abs=0.05)


def test_two_per_second_for_four_seconds_is_no_advisory() -> None:
    """SPEC T7: a 210-frame span (3.5 s) is not longer than 5 s."""
    assert _advisories(find_advisories(_every(240, 30), FPS)) == []


def test_span_of_exactly_five_seconds_is_no_advisory() -> None:
    """SPEC T7: 'longer than 5 s' is exclusive - a span of exactly 300 frames (30..329) does not count."""
    assert _advisories(find_advisories(_every(330, 30), FPS)) == []


def test_span_one_frame_longer_than_five_seconds_is_an_advisory() -> None:
    """SPEC T7: 301 frames (30..330) is longer than 5 s, so the advisory appears."""
    result = _advisories(find_advisories(_every(331, 30), FPS))
    assert len(result) == 1
    assert (result[0][0], result[0][1]) == (30, 330)


def test_advisory_needs_the_rate_at_every_frame_of_the_span() -> None:
    """SPEC 5.9: 'trailing[t] >= 2 at every frame' - one missing event breaks a 7 s span in two."""
    events = _every(420, 30)
    events[210] = False
    assert _advisories(find_advisories(events, FPS)) == []


def test_advisory_min_rate_parameter() -> None:
    """SPEC T7 parameter: min_rate=3 ignores the 2/s pattern and catches the 3/s pattern."""
    assert _advisories(find_advisories(_every(420, 30), FPS, min_rate=3)) == []
    result = _advisories(find_advisories(_every(420, 20), FPS, min_rate=3))
    assert len(result) == 1
    assert (result[0][0], result[0][1]) == (40, 419)


def test_advisory_min_seconds_parameter() -> None:
    """SPEC T7 parameter: with min_seconds=3.0 the 210-frame span (3.5 s) becomes an advisory."""
    result = _advisories(find_advisories(_every(240, 30), FPS, min_seconds=3.0))
    assert len(result) == 1
    assert (result[0][0], result[0][1]) == (30, 239)


def test_three_per_second_sustained_is_also_an_advisory() -> None:
    """ADR-009: 3/s is not a WCAG failure, but sustained for 7 s it is still advised."""
    result = _advisories(find_advisories(_every(420, 20), FPS))
    assert len(result) == 1
    start, end, mean_rate = result[0]
    assert (start, end) == (20, 419)
    assert mean_rate >= 2.0


def test_no_events_means_no_advisories() -> None:
    """SPEC 5.9: nothing flashing can never sustain two per second."""
    assert find_advisories(np.zeros(600, dtype=bool), FPS) == []


def test_advisory_entries_have_int_bounds_and_float_rate() -> None:
    """Contract: (start, end, mean_rate_per_sec) is (int, int, float)."""
    (advisory,) = find_advisories(_every(420, 30), FPS)
    start, end, mean_rate = advisory
    assert float(start) == int(start)
    assert float(end) == int(end)
    assert math.isfinite(float(mean_rate))


# ---------------------------------------------------------------------------
# timeline (SPEC 5.8)
# ---------------------------------------------------------------------------


def test_timeline_bins_including_a_partial_last_second() -> None:
    """SPEC 5.8: one bin per second k over [k*fps, (k+1)*fps); a trailing partial second still gets a bin."""
    general = _events(150, [0, 59, 60, 119, 120, 149])
    red = _events(150, [30, 130])
    assert _timeline(timeline(general, red, FPS)) == [(0.0, 2, 1), (1.0, 2, 0), (2.0, 2, 1)]


def test_timeline_when_duration_is_a_whole_number_of_seconds() -> None:
    """SPEC 5.8: 120 frames at 60 fps are exactly two bins."""
    general = _events(120, [0, 1, 2, 119])
    red = np.zeros(120, dtype=bool)
    assert _timeline(timeline(general, red, FPS)) == [(0.0, 3, 0), (1.0, 1, 0)]


def test_timeline_t_is_in_seconds_for_other_frame_rates() -> None:
    """SPEC 5.1: frame i is at i/fps seconds, so at 30 fps the bins start at 0, 1, 2 s."""
    general = _events(90, [0, 29, 30, 89])
    red = _events(90, [45])
    assert _timeline(timeline(general, red, 30)) == [(0.0, 2, 0), (1.0, 1, 1), (2.0, 1, 0)]


def test_timeline_with_no_events_is_all_zero() -> None:
    """SPEC 5.8: every bin exists even when nothing flashed."""
    zeros = np.zeros(150, dtype=bool)
    assert _timeline(timeline(zeros, zeros, FPS)) == [(0.0, 0, 0), (1.0, 0, 0), (2.0, 0, 0)]


def test_timeline_keeps_general_and_red_separate() -> None:
    """SPEC T5: 'the two types are counted separately and never summed'."""
    general = _events(60, [5, 6])
    red = _events(60, [5, 6, 7])
    assert _timeline(timeline(general, red, FPS)) == [(0.0, 2, 3)]


@_PROPERTY
@given(
    st.integers(min_value=1, max_value=10),
    st.lists(st.tuples(st.booleans(), st.booleans()), min_size=1, max_size=40),
)
def test_property_timeline_bins_sum_to_the_event_totals(fps: int, pairs: list[tuple[bool, bool]]) -> None:
    """SPEC 5.8: every event lands in exactly one bin, so the bins sum to the totals."""
    general = np.array([g for g, _ in pairs], dtype=bool)
    red = np.array([r for _, r in pairs], dtype=bool)
    bins = _timeline(timeline(general, red, fps))
    assert len(bins) == math.ceil(len(pairs) / fps)
    assert [t for t, _, _ in bins] == [float(k) for k in range(len(bins))]
    assert sum(g for _, g, _ in bins) == int(general.sum())
    assert sum(r for _, _, r in bins) == int(red.sum())
