"""Rate: how many qualifying flashes fall inside any one-second window? (SPEC 5.7, 5.9)

WCAG 2.2 SC 2.3.1 forbids "anything that flashes more than three times in any
one second period".  "Any one second period" is a sliding window, so every
window of ``fps`` consecutive frames is counted, not just whole clock seconds.
All counting is done with a cumulative total (prefix sum): the number of
events in frames [a, b] is total[b+1] - total[a], which is exact integer
arithmetic and costs the same for every window.
"""

from __future__ import annotations

import numpy as np

from engine.constants import (
    ADVISORY_MIN_DURATION_SECONDS,
    ADVISORY_MIN_FLASHES_PER_SECOND,
    MAX_FLASHES_PER_SECOND,
)


def _as_events(events: np.ndarray) -> np.ndarray:
    e = np.asarray(events, dtype=bool).reshape(-1)
    return e


def _check_fps(fps: int) -> int:
    if int(fps) < 1:
        raise ValueError(f"fps must be >= 1, got {fps}")
    return int(fps)


def _cumulative(events: np.ndarray) -> np.ndarray:
    """total[k] = number of events in frames 0 .. k-1 (so total[0] = 0)."""
    e = _as_events(events)
    total = np.zeros(e.size + 1, dtype=np.int64)
    np.cumsum(e, dtype=np.int64, out=total[1:])
    return total


def _true_runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """Maximal runs of True in a 1-D bool array as (first index, last index)."""
    padded = np.concatenate(([False], np.asarray(mask, dtype=bool), [False]))
    edges = np.diff(padded.astype(np.int8))
    starts = np.flatnonzero(edges == 1)
    ends = np.flatnonzero(edges == -1) - 1
    return list(zip(starts.tolist(), ends.tolist()))


def trailing_counts(events: np.ndarray, fps: int) -> np.ndarray:
    """Events in the one second ENDING at each frame, [t-fps+1, t] -> int64[T]."""
    fps = _check_fps(fps)
    total = _cumulative(events)
    frames = total.size - 1
    ends = np.arange(1, frames + 1, dtype=np.int64)       # exclusive end = t + 1
    starts = np.maximum(0, ends - fps)                    # inclusive start, clipped at 0
    return total[ends] - total[starts]


def window_counts(events: np.ndarray, fps: int) -> np.ndarray:
    """Events in the one second STARTING at each window start -> int64[max(1, T-fps+1)].

    When the capture is shorter than one second there is a single window
    covering the whole capture (SPEC 5.7).
    """
    fps = _check_fps(fps)
    total = _cumulative(events)
    frames = total.size - 1
    n_windows = max(1, frames - fps + 1)
    starts = np.arange(n_windows, dtype=np.int64)
    ends = np.minimum(frames, starts + fps)               # exclusive, clipped at T
    return total[ends] - total[starts]


def find_violations(
    events: np.ndarray, fps: int, max_per_second: int = MAX_FLASHES_PER_SECOND
) -> list[tuple[int, int, int]]:
    """Maximal spans of failing window starts -> [(start_frame, end_frame, max_count)].

    end_frame is the last frame covered by the last failing window
    (last start + fps - 1), clipped to the capture.
    """
    fps = _check_fps(fps)
    frames = _as_events(events).size
    counts = window_counts(events, fps)
    # WCAG 2.2 SC 2.3.1: "flashes more than three times in any one second
    # period"  -> exclusive: a window fails only when its count is > 3.
    failing = counts > max_per_second
    violations: list[tuple[int, int, int]] = []
    for first, last in _true_runs(failing):
        end_frame = min(frames - 1, last + fps - 1)
        violations.append((first, end_frame, int(counts[first : last + 1].max())))
    return violations


def find_advisories(
    events: np.ndarray,
    fps: int,
    min_rate: int = ADVISORY_MIN_FLASHES_PER_SECOND,
    min_seconds: float = ADVISORY_MIN_DURATION_SECONDS,
) -> list[tuple[int, int, float]]:
    """Sustained-flashing spans (NOT a WCAG rule, ADR-009) -> [(start, end, mean_rate)].

    A span is every frame whose trailing one-second count is >= min_rate, and
    it is reported only when it lasts LONGER than min_seconds.  mean_rate is
    the average of those trailing counts over the span, i.e. the average
    flashes-per-second the content sustained.
    """
    fps = _check_fps(fps)
    trailing = trailing_counts(events, fps)
    # Advisory threshold (FlashTrace, modelled on IRIS): at least 2 per second ...
    sustained = trailing >= min_rate
    advisories: list[tuple[int, int, float]] = []
    for first, last in _true_runs(sustained):
        length = last - first + 1
        # ... for longer than 5 seconds (exclusive: exactly 5 s is not reported).
        if length > min_seconds * fps:
            advisories.append((first, last, float(trailing[first : last + 1].mean())))
    return advisories


def timeline(
    events_general: np.ndarray, events_red: np.ndarray, fps: int
) -> list[tuple[float, int, int]]:
    """Qualifying events per second -> [(t_seconds, general, red)].

    Bin k covers frames [k*fps, (k+1)*fps).  A final partial second gets its
    own bin with its raw count, so no event is left out and the bins add up
    to the totals (ADR-021).
    """
    fps = _check_fps(fps)
    general = _as_events(events_general)
    red = _as_events(events_red)
    if general.size != red.size:
        raise ValueError(f"event arrays differ in length: {general.size} vs {red.size}")
    frames = general.size
    n_bins = -(-frames // fps)  # ceiling division
    bins: list[tuple[float, int, int]] = []
    for k in range(n_bins):
        lo, hi = k * fps, min(frames, (k + 1) * fps)
        bins.append((float(k), int(general[lo:hi].sum()), int(red[lo:hi].sum())))
    return bins
