"""Area: does a flash cover more than 25 % of some 341x256 px window? (SPEC 5.6)

WCAG 2.2 glossary: content passes when the flashing area "occupies no more
than ... 25% of any 10 degree visual field on the screen", and Note 1 says a
"341 x 256 pixel rectangle anywhere on the displayed screen area" stands in
for that field.  "Anywhere" means every window position must be tried.  An
integral image (a running 2-D total) answers "how many flashing cells are in
this window?" for every position with four look-ups each, so the check costs
about as much as reading the grid once and does not depend on where the
flashing region happens to be (translation invariance, ADR-006).
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

    area_max: np.ndarray        # float64: best window's flashing fraction, count / (wy*wx)
    window_y: np.ndarray        # int64: top cell row of that best window
    window_x: np.ndarray        # int64: left cell column of that best window
    qualifying_raw: np.ndarray  # bool: area_max > MAX_AREA_FRACTION
    qualifying: np.ndarray      # bool: qualifying_raw on its leading edge only (counted once)


def qualifying_events(
    masks: np.ndarray,
    wy: int,
    wx: int,
    tolerance_frames: int = EVENT_FRAME_TOLERANCE,
    max_fraction: float = MAX_AREA_FRACTION,
) -> AreaResult:
    """Score the flash masks of every frame and mark the qualifying events.

    masks[t, y, x] is True when cell (y, x) completed a flash at frame t.
    Steps (SPEC 5.6):
      1. E[t] = M[t] | M[t+1] | ... | M[t+tolerance_frames]: completions a
         frame apart belong to the same event.
      2. area_max[t] = largest window count / window size.
      3. qualifying_raw[t] = area_max[t] > max_fraction.
      4. qualifying[t] = qualifying_raw[t] and not qualifying_raw[t-1]: one
         event is counted once, at the first frame it qualifies.
    """
    m = np.asarray(masks, dtype=bool)
    if m.ndim != 3:
        raise ValueError(f"masks must be shaped [T, cy, cx], got {m.shape}")
    if tolerance_frames < 0:
        raise ValueError(f"tolerance_frames must be >= 0, got {tolerance_frames}")
    frames = m.shape[0]
    window_size = float(wy * wx)

    events = m.copy()
    for k in range(1, tolerance_frames + 1):
        if k < frames:
            events[:-k] |= m[k:]

    area_max = np.zeros(frames, dtype=np.float64)
    window_y = np.zeros(frames, dtype=np.int64)
    window_x = np.zeros(frames, dtype=np.int64)
    for t in range(frames):
        if not events[t].any():
            continue  # nothing flashed: count 0 at window (0, 0)
        count, y, x = best_window(events[t], wy, wx)
        area_max[t] = count / window_size
        window_y[t] = y
        window_x[t] = x

    # WCAG 2.2 glossary: passes when the area is "no more than ... 25% of any 10
    # degree visual field"  -> an event qualifies only when STRICTLY greater.
    qualifying_raw = area_max > max_fraction
    previous = np.concatenate(([False], qualifying_raw[:-1]))
    qualifying = qualifying_raw & ~previous
    return AreaResult(
        area_max=area_max,
        window_y=window_y,
        window_x=window_x,
        qualifying_raw=qualifying_raw,
        qualifying=qualifying,
    )
