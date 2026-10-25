"""Area: does a flash cover more than 25 % of some 341x256 px window? (SPEC 5.6)

WCAG 2.2 glossary: content passes when the flashing area "occupies no more
than ... 25% of any 10 degree visual field on the screen", and Note 1 says a
"341 x 256 pixel rectangle anywhere on the displayed screen area" stands in
for that field.  "Anywhere" means every window position must be tried.  An
integral image (a running 2-D total) answers "how many flashing cells are in
this window?" for every position with four look-ups each, so the check costs
about as much as reading the grid once and does not depend on where the
flashing region happens to be (translation invariance, ADR-006).

The share is always taken of the FULL window (5 440 cells at 1024 px wide),
even when a short viewport forces the scanned window to be clipped: WCAG's
25 % is an absolute area of 21 824 px (ADR-019).

Counting (SPEC 5.6 step 4): an event counted at frame t claims the
completions of frames t and t+1, and a later frame is scored only on the
completions that no event has claimed yet.  So one flash is counted once
even when its cells finish on two neighbouring frames.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from engine.constants import EVENT_FRAME_TOLERANCE, MAX_AREA_FRACTION


def integral_image(mask: np.ndarray) -> np.ndarray:
    """bool [cy, cx] -> int64 [cy+1, cx+1] where ii[y, x] = count of True above-left of (y, x)."""
    m = np.asarray(mask)
    if m.ndim != 2:
        raise ValueError(f"mask must be 2-D, got shape {m.shape}")
    ii = np.zeros((m.shape[0] + 1, m.shape[1] + 1), dtype=np.int64)
    # Row totals first, then column totals: ii[y, x] is the sum of the whole
    # rectangle from the top-left corner to (but excluding) row y and column x.
    ii[1:, 1:] = m.astype(np.int64).cumsum(axis=0).cumsum(axis=1)
    return ii


def window_sums(ii: np.ndarray, wy: int, wx: int) -> np.ndarray:
    """Count of True cells in every (wy x wx) window -> int64 [cy-wy+1, cx-wx+1]."""
    cy, cx = ii.shape[0] - 1, ii.shape[1] - 1
    if not (1 <= wy <= cy and 1 <= wx <= cx):
        raise ValueError(f"window {wy}x{wx} does not fit a {cy}x{cx} grid")
    # The classic four-corner formula: total below-right minus the two strips
    # above and to the left, plus the corner that was subtracted twice.
    return ii[wy:, wx:] - ii[:-wy, wx:] - ii[wy:, :-wx] + ii[:-wy, :-wx]


def best_window(mask: np.ndarray, wy: int, wx: int) -> tuple[int, int, int]:
    """The window position holding the most True cells: (count, top y, left x).

    Ties go to the smallest y, then the smallest x, because np.argmax returns
    the first maximum in row-major order.
    """
    sums = window_sums(integral_image(mask), wy, wx)
    flat_index = int(np.argmax(sums))
    y, x = divmod(flat_index, sums.shape[1])
    return int(sums[y, x]), int(y), int(x)


@dataclass
class AreaResult:
    """Per-frame area scoring for one flash type; every array has length T."""

    area_max: np.ndarray        # float64: best window's flashing fraction, count / full window cells
    window_y: np.ndarray        # int64: top cell row of that best window
    window_x: np.ndarray        # int64: left cell column of that best window
    qualifying_raw: np.ndarray  # bool: area_max > MAX_AREA_FRACTION
    qualifying: np.ndarray      # bool: a qualifying event is counted at this frame


def union_with_previous_frames(masks: np.ndarray, tolerance_frames: int) -> np.ndarray:
    """SPEC 5.6 step 1: E[t] = M[t - tolerance_frames] | ... | M[t-1] | M[t].

    In: bool masks [T, cy, cx].  Out: bool [T, cy, cx].  Looking backwards
    keeps each event on the frame where its flash completes (ADR-019);
    frames before frame 0 count as empty.
    """
    events = masks.copy()
    for k in range(1, tolerance_frames + 1):
        if k < masks.shape[0]:
            # Frame t also takes in the cells that completed k frames earlier.
            events[k:] |= masks[:-k]
    return events


def score_windows(
    events: np.ndarray, wy: int, wx: int, full_window_cells: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """SPEC 5.6 step 2: per frame, the best window's share of the full window.

    In: bool events [T, cy, cx], the scanned window (wy, wx) and the size of
    the full window in cells.  Out: area_max float64[T], window_y int64[T],
    window_x int64[T] (top-left cell of the best window; (0, 0) when nothing
    flashed).
    """
    frames = events.shape[0]
    area_max = np.zeros(frames, dtype=np.float64)
    window_y = np.zeros(frames, dtype=np.int64)
    window_x = np.zeros(frames, dtype=np.int64)
    for t in range(frames):
        if not events[t].any():
            continue  # nothing flashed: count 0 at window (0, 0)
        count, y, x = best_window(events[t], wy, wx)
        area_max[t] = count / float(full_window_cells)
        window_y[t] = y
        window_x[t] = x
    return area_max, window_y, window_x


def unclaimed_share(
    masks: np.ndarray,
    t: int,
    claimed: np.ndarray,
    tolerance_frames: int,
    window: tuple[int, int],
    full_window_cells: int,
) -> float:
    """Best window share of the completions at frames t - tolerance_frames .. t that no event has claimed.

    In: bool masks [T, cy, cx], the frame t, bool claimed [>= T], the
    tolerance, the scanned window (wy, wx) and the full window size in cells.
    Out: the largest window count divided by the full window size.
    """
    unclaimed = np.zeros(masks.shape[1:], dtype=bool)
    for k in range(max(0, t - tolerance_frames), t + 1):
        if not claimed[k]:
            unclaimed |= masks[k]  # frame k's completions are still free to form an event
    count, _, _ = best_window(unclaimed, window[0], window[1])
    return count / float(full_window_cells)


def count_events(
    masks: np.ndarray,
    qualifying_raw: np.ndarray,
    tolerance_frames: int,
    window: tuple[int, int],
    full_window_cells: int,
    max_fraction: float = MAX_AREA_FRACTION,
) -> np.ndarray:
    """SPEC 5.6 step 4: the frames at which qualifying events are counted (ADR-019).

    In: bool masks [T, cy, cx], bool qualifying_raw [T] (step 3), the
    tolerance, the scanned window (wy, wx) and the full window size.
    Out: bool [T].  An event counted at frame t claims frames t .. t +
    tolerance_frames: their completions belong to it.  A later frame is
    scored using only completions no earlier event has claimed, so one flash
    whose cells complete on two neighbouring frames is counted once.
    """
    frames = masks.shape[0]
    counted = np.zeros(frames, dtype=bool)
    # claimed[k] is True once frame k's completions belong to a counted event.
    # Spare slots at the end let the last event claim frames past the capture.
    claimed = np.zeros(frames + tolerance_frames, dtype=bool)
    for t in range(frames):
        # The unclaimed completions are part of E[t], so they can never score
        # more than area_max[t]: a frame that failed step 3 cannot hold an event.
        if claimed[t] or not qualifying_raw[t]:
            continue
        # WCAG 2.2 glossary: passes when the area is "no more than ... 25% of any
        # 10 degree visual field"  -> an event needs STRICTLY more than max_fraction.
        if unclaimed_share(masks, t, claimed, tolerance_frames, window, full_window_cells) > max_fraction:
            counted[t] = True
            claimed[t : t + tolerance_frames + 1] = True
    return counted


def qualifying_events(
    masks: np.ndarray,
    wy: int,
    wx: int,
    tolerance_frames: int = EVENT_FRAME_TOLERANCE,
    max_fraction: float = MAX_AREA_FRACTION,
    full_window_cells: int | None = None,
) -> AreaResult:
    """Score the flash masks of every frame and mark the qualifying events.

    masks[t, y, x] is True when cell (y, x) completed a flash at frame t;
    (wy, wx) is the scanned window; full_window_cells is the unclipped window
    size, the area denominator (None means wy * wx: the window was not clipped).
    Steps (SPEC 5.6, ADR-019):
      1. E[t] = M[t-1] | M[t]: completions a frame apart are one event.
      2. area_max[t] = largest window count / full window size.
      3. qualifying_raw[t] = area_max[t] > max_fraction.
      4. an event counted at t claims frames t and t+1; a frame is scored
         again using only the completions no earlier event has claimed.
    """
    m = np.asarray(masks, dtype=bool)
    if m.ndim != 3:
        raise ValueError(f"masks must be shaped [T, cy, cx], got {m.shape}")
    if tolerance_frames < 0:
        raise ValueError(f"tolerance_frames must be >= 0, got {tolerance_frames}")
    denominator = wy * wx if full_window_cells is None else int(full_window_cells)
    if denominator < wy * wx:
        raise ValueError(f"full window ({denominator} cells) is smaller than the scanned {wy}x{wx}")
    events = union_with_previous_frames(m, tolerance_frames)
    area_max, window_y, window_x = score_windows(events, wy, wx, denominator)
    # WCAG 2.2 glossary: passes when the area is "no more than ... 25% of any 10
    # degree visual field"  -> an event qualifies only when STRICTLY greater.
    qualifying_raw = area_max > max_fraction
    counted = count_events(m, qualifying_raw, tolerance_frames, (wy, wx), denominator, max_fraction)
    return AreaResult(
        area_max=area_max,
        window_y=window_y,
        window_x=window_x,
        qualifying_raw=qualifying_raw,
        qualifying=counted,
    )
