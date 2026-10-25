"""Turn each cell's signal-over-time into transitions and flashes (SPEC 5.4, 5.5).

WCAG 2.2 glossary Note 2 defines the vocabulary: a transition is "the change in
relative luminance (or relative luminance/color for red flashing) between
adjacent peaks and valleys in a plot of ... measurement against time", and
"A flash consists of two opposing transitions".

So the work is:
  1. find every cell's peaks and valleys as the frames stream past,
  2. when a peak-to-valley (or valley-to-peak) run ends, ask the rule for that
     signal whether the change was big enough - if so it is a *transition*,
  3. pair each transition with the next opposite one into a *flash*.

RunTracker does 1 and 2 for all N cells at once with a handful of NumPy
operations per frame; FlashPairer does 3.  Both keep only O(N) state, so the
engine never needs every frame in memory.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np

from engine.constants import (
    GENERAL_DARK_STATE_MAX_L,
    GENERAL_MIN_DELTA_L,
    LEGACY_RED_MIN_DELTA,
    RED_MIN_DELTA_UV,
    RED_SATURATION_MIN_RATIO,
    RUN_REVERSAL_TOLERANCE,
)

# A rule looks at the two end states of a run (a = where the run started,
# b = where it ended) and says, per cell, whether the change is a transition.
# ``aux`` carries any extra per-cell numbers the rule needs besides the signal.
Rule = Callable[
    [np.ndarray, np.ndarray, dict[str, np.ndarray], dict[str, np.ndarray]], np.ndarray
]


def general_rule(
    sig_a: np.ndarray, sig_b: np.ndarray, aux_a: dict[str, np.ndarray], aux_b: dict[str, np.ndarray]
) -> np.ndarray:
    """General flash transition test on relative luminance L (T2)."""
    # WCAG 2.2 glossary: "a pair of opposing changes in relative luminance of 10%
    # or more of the maximum relative luminance (1.0)"  -> inclusive >=
    big_enough = np.abs(sig_b - sig_a) >= GENERAL_MIN_DELTA_L
    # WCAG 2.2 glossary: "where the relative luminance of the darker image is
    # below 0.80"  -> exclusive <
    dark_enough = np.minimum(sig_a, sig_b) < GENERAL_DARK_STATE_MAX_L
    return big_enough & dark_enough


def red_rule_wcag22(
    sig_a: np.ndarray, sig_b: np.ndarray, aux_a: dict[str, np.ndarray], aux_b: dict[str, np.ndarray]
) -> np.ndarray:
    """Red flash transition test, WCAG 2.2 working definition (T3).

    Signal: redness ratio R/(R+G+B).  aux: "u", "v" (CIE 1976 UCS).
    """
    # WCAG 2.2 glossary Note 3: "one transition is either to or from a state
    # with a value R/(R + G + B) that is greater than or equal to 0.8"
    touches_saturated_red = np.maximum(sig_a, sig_b) >= RED_SATURATION_MIN_RATIO
    # WCAG 2.2 glossary Note 3: "the difference between states is more than 0.2
    # (unitless) in the CIE 1976 UCS chromaticity diagram"; Understanding 2.3.1:
    # "SQRT( (u'1 - u'2)^2 + (v'1 - v'2)^2 )"
    colour_change = np.hypot(aux_b["u"] - aux_a["u"], aux_b["v"] - aux_a["v"])
    return touches_saturated_red & (colour_change > RED_MIN_DELTA_UV)


def red_rule_legacy(
    sig_a: np.ndarray, sig_b: np.ndarray, aux_a: dict[str, np.ndarray], aux_b: dict[str, np.ndarray]
) -> np.ndarray:
    """Red flash transition test, pre-2022 working definition (comparison only, ADR-001).

    Signal: legacy red value max(0, R-G-B) x 320.  aux: "ratio".
    """
    # Same saturated-red condition as the current rule: one end has R/(R+G+B) >= 0.8
    touches_saturated_red = np.maximum(aux_a["ratio"], aux_b["ratio"]) >= RED_SATURATION_MIN_RATIO
    # Build brief / WCAG 2.0-2.1 era: a change in "(R - G - B) x 320" that is
    # "greater than 20"  -> exclusive >
    value_change = np.abs(sig_b - sig_a) > LEGACY_RED_MIN_DELTA
    return touches_saturated_red & value_change


@dataclass
class Transition:
    """One group of cells that all made a transition at the same frame, same direction."""

    frame: int        # the run's stamp frame: where it reached its extremum, ignoring gains <= tolerance
    sign: int         # +1 the signal rose, -1 it fell
    cells: np.ndarray  # int indices (row-major cell ids), ascending, no duplicates


class RunTracker:
    """Find peaks and valleys of N signals at once, one frame at a time.

    Per cell the tracker remembers (SPEC 5.4, ADR-004, ADR-018):

      base      - value, aux and frame of the previous extremum (where the
                  current run started),
      direction - 0 nothing has moved yet, +1 rising, -1 falling,
      ext       - value and aux of the best extremum seen so far in the
                  current run (the highest point while rising, the lowest
                  while falling),
      stamp     - frame and value of the frame the transition will be
                  stamped with: where the signal arrived at its extremum,
      settled   - True once the signal has stood still or wobbled back
                  since the stamp, i.e. it has arrived.

    On each new frame with value s (rising; falling is the mirror image):
      s < ext - tol      -> the run ENDED: evaluate rule(base, ext), emit a
                            Transition(stamp.frame, +1) if it holds, then
                            base = ext, direction = -1, ext = stamp = s at t
      s > ext            -> the run continues; ext = (s, aux).  The stamp
                            moves to t if the signal has not settled yet, or
                            if s > stamp + tol (a real step, not noise)
      otherwise          -> plateau, or a reversal of at most tol: the run
                            goes on and the signal is now settled
    flat (nothing has moved yet): s > base + tol starts a rise at t,
    s < base - tol starts a fall.

    tol is the signal's reversal tolerance (RUN_REVERSAL_TOLERANCE, ADR-018).
    The rule always sees the true extremum (ext).  The stamp follows a smooth
    fade all the way to its peak or valley, but once the signal has arrived,
    wobble no larger than tol can neither end the run nor move its stamp.
    For a square wave the stamp is the FIRST frame showing the new state; it
    is always EARLIER than the frame that revealed the reversal.
    """

    def __init__(self, n_cells: int, rule: Rule, tolerance: float) -> None:
        if n_cells < 1:
            raise ValueError(f"n_cells must be >= 1, got {n_cells}")
        if tolerance < 0:
            raise ValueError(f"tolerance must be >= 0, got {tolerance}")
        self.n_cells: int = int(n_cells)
        self.rule: Rule = rule
        self.tolerance: float = float(tolerance)
        self.direction: np.ndarray = np.zeros(self.n_cells, dtype=np.int8)
        self.base_val: np.ndarray = np.zeros(self.n_cells, dtype=np.float64)
        self.base_frame: np.ndarray = np.zeros(self.n_cells, dtype=np.int64)
        self.base_aux: dict[str, np.ndarray] = {}
        self.ext_val: np.ndarray = np.zeros(self.n_cells, dtype=np.float64)
        self.ext_aux: dict[str, np.ndarray] = {}
        self.stamp_val: np.ndarray = np.zeros(self.n_cells, dtype=np.float64)
        self.stamp_frame: np.ndarray = np.zeros(self.n_cells, dtype=np.int64)
        self.settled: np.ndarray = np.zeros(self.n_cells, dtype=bool)
        self._started: bool = False
        self._finished: bool = False
        self._last_t: int = -1

    # -- input checking -----------------------------------------------------

    def _check(self, t: int, signal: np.ndarray, aux: dict[str, np.ndarray]) -> np.ndarray:
        if self._finished:
            raise ValueError("RunTracker.update() called after finish()")
        if t <= self._last_t:
            raise ValueError(f"frames must be strictly increasing, got {t} after {self._last_t}")
        s = np.asarray(signal, dtype=np.float64)
        if s.shape != (self.n_cells,):
            raise ValueError(f"signal must be shaped ({self.n_cells},), got {s.shape}")
        for key, arr in aux.items():
            if np.shape(arr) != (self.n_cells,):
                raise ValueError(f"aux[{key!r}] must be shaped ({self.n_cells},), got {np.shape(arr)}")
        if self._started and set(aux) != set(self.base_aux):
            raise ValueError(f"aux keys changed from {sorted(self.base_aux)} to {sorted(aux)}")
        return s

    # -- state helpers ------------------------------------------------------

    def _start(self, t: int, s: np.ndarray, aux: dict[str, np.ndarray]) -> None:
        """The first frame is both the base and the extremum of every cell."""
        self.base_val[:] = s
        self.ext_val[:] = s
        self.stamp_val[:] = s
        self.base_frame[:] = t
        self.stamp_frame[:] = t
        self.base_aux = {k: np.array(v, dtype=np.float64) for k, v in sorted(aux.items())}
        self.ext_aux = {k: np.array(v, dtype=np.float64) for k, v in sorted(aux.items())}
        self._started = True

    def _set_extremum(self, mask: np.ndarray, s: np.ndarray, aux: dict[str, np.ndarray]) -> None:
        """For the masked cells, the current value becomes the run's extremum."""
        self.ext_val[mask] = s[mask]
        for key in self.ext_aux:
            self.ext_aux[key][mask] = np.asarray(aux[key], dtype=np.float64)[mask]

    def _move_stamps(
        self, t: int, s: np.ndarray, new_run: np.ndarray, extend: np.ndarray, big_step: np.ndarray,
        paused: np.ndarray,
    ) -> None:
        """Decide per cell whether frame t becomes the frame its run is stamped with (ADR-018).

        A run that starts at t is stamped at t.  While the signal is still
        arriving (not settled) every new extremum moves the stamp, so a smooth
        fade is followed all the way to its peak or valley.  Once the signal
        has paused or wobbled back by no more than the tolerance it has
        settled: then only a big step (more than the tolerance beyond the
        stamp value) moves the stamp, and the signal counts as arriving again.
        """
        restamp = new_run | big_step | (extend & ~self.settled)
        self.stamp_val[restamp] = s[restamp]
        self.stamp_frame[restamp] = t
        self.settled[restamp] = False
        self.settled[paused] = True

    def _promote_extremum_to_base(self, mask: np.ndarray) -> None:
        """For the masked cells, the run that just ended becomes the start of the next."""
        self.base_val[mask] = self.ext_val[mask]
        self.base_frame[mask] = self.stamp_frame[mask]
        for key in self.base_aux:
            self.base_aux[key][mask] = self.ext_aux[key][mask]

    def _close_runs(self, mask: np.ndarray, sign: int) -> list[Transition]:
        """Evaluate the rule on base -> ext for the masked cells; group hits by frame."""
        idx = np.flatnonzero(mask)
        if idx.size == 0:
            return []
        base_aux = {k: v[idx] for k, v in self.base_aux.items()}
        ext_aux = {k: v[idx] for k, v in self.ext_aux.items()}
        passed = np.asarray(
            self.rule(self.base_val[idx], self.ext_val[idx], base_aux, ext_aux), dtype=bool
        )
        hits = idx[passed]
        if hits.size == 0:
            return []
        # Different cells may have reached their extremum on different frames;
        # one Transition per distinct frame.  A stable sort keeps cell ids
        # ascending inside each group, so the output order is fully determined.
        frames = self.stamp_frame[hits]
        order = np.argsort(frames, kind="stable")
        hits, frames = hits[order], frames[order]
        unique_frames, starts, counts = np.unique(frames, return_index=True, return_counts=True)
        return [
            Transition(frame=int(f), sign=sign, cells=hits[st : st + c])
            for f, st, c in zip(unique_frames.tolist(), starts.tolist(), counts.tolist())
        ]

    # -- public API ---------------------------------------------------------

    def update(self, t: int, signal: np.ndarray, aux: dict[str, np.ndarray]) -> list[Transition]:
        """Feed frame ``t``.  Returns the transitions whose runs ended on this frame."""
        s = self._check(t, signal, aux)
        self._last_t = int(t)
        if not self._started:
            self._start(t, s, aux)
            return []

        tol = self.tolerance
        rising = self.direction == 1
        falling = self.direction == -1
        flat = self.direction == 0

        # Runs that end now: the signal turned around by more than the tolerance (ADR-018).
        end_rise = rising & (s < self.ext_val - tol)
        end_fall = falling & (s > self.ext_val + tol)
        # Runs that push on to a new extremum, by any amount: the rule must see
        # the true peak or valley.
        extend = (rising & (s > self.ext_val)) | (falling & (s < self.ext_val))
        # Runs that went beyond their stamp value by MORE than the tolerance: a real step, not noise.
        big_step = (rising & (s > self.stamp_val + tol)) | (falling & (s < self.stamp_val - tol))
        # Runs that stood still or wobbled back by no more than the tolerance: the signal has arrived.
        paused = (rising | falling) & ~extend & ~end_rise & ~end_fall
        # Cells that have not moved yet and now leave the tolerance band.
        start_rise = flat & (s > self.base_val + tol)
        start_fall = flat & (s < self.base_val - tol)

        emitted = self._close_runs(end_rise, +1) + self._close_runs(end_fall, -1)

        ended = end_rise | end_fall
        self._promote_extremum_to_base(ended)
        self.direction[end_rise] = -1
        self.direction[end_fall] = 1
        self.direction[start_rise] = 1
        self.direction[start_fall] = -1
        new_run = ended | start_rise | start_fall  # these cells start a run at frame t
        self._set_extremum(new_run | extend, s, aux)
        self._move_stamps(t, s, new_run, extend, big_step, paused)
        return emitted

    def finish(self, t_last: int) -> list[Transition]:
        """Close every run still open after the last frame (ADR-004).

        The transition is timestamped at the run's stamp frame, exactly as
        in update(); ``t_last`` is checked to be the last frame fed in.
        """
        if self._finished:
            raise ValueError("RunTracker.finish() called twice")
        if not self._started:
            self._finished = True
            return []
        if t_last != self._last_t:
            raise ValueError(f"finish({t_last}) does not match the last frame fed ({self._last_t})")
        emitted = self._close_runs(self.direction == 1, +1) + self._close_runs(self.direction == -1, -1)
        self.direction[:] = 0
        self._finished = True
        return emitted


class FlashPairer:
    """Pair each cell's transitions into flashes (SPEC 5.5, ADR-005, ADR-018).

    WCAG 2.2 glossary: a flash is "a pair of opposing changes".  Transitions
    are paired in order, non-overlapping (1st+2nd, 3rd+4th, ...), and only
    opposing transitions pair.  A cell's pending transition is remembered by
    its sign; when the opposite sign arrives the pair is complete and the
    flash is counted at that frame.  Two same-sign transitions in a row are
    possible when a run below the threshold sits between them (rise, small
    dip, rise); the newer one then replaces the pending one (ADR-018), so a
    flash is always one rise and one fall.
    """

    def __init__(self, n_cells: int) -> None:
        if n_cells < 1:
            raise ValueError(f"n_cells must be >= 1, got {n_cells}")
        self.n_cells: int = int(n_cells)
        self.pending_sign: np.ndarray = np.zeros(self.n_cells, dtype=np.int8)

    def add(self, transition: Transition) -> np.ndarray:
        """Record a transition; return the cells whose flash it COMPLETES."""
        cells = np.asarray(transition.cells, dtype=np.intp)
        if transition.sign not in (1, -1):
            raise ValueError(f"sign must be +1 or -1, got {transition.sign}")
        pending = self.pending_sign[cells]
        completes = pending == -transition.sign
        completed = cells[completes]
        self.pending_sign[completed] = 0
        self.pending_sign[cells[~completes]] = transition.sign
        return completed


def _aux_frame(aux: dict[str, np.ndarray], t: int) -> dict[str, np.ndarray]:
    """Frame t of every aux series, each as a one-cell array."""
    return {k: v[t : t + 1] for k, v in aux.items()}


def detect_transitions_1d(
    signal: np.ndarray,
    rule: Rule = general_rule,
    tolerance: float = RUN_REVERSAL_TOLERANCE["L"],
    aux: dict[str, np.ndarray] | None = None,
) -> list[tuple[int, int]]:
    """Transitions of ONE cell's time series as (frame, sign) pairs, in frame order.

    For tests and teaching: analyze() never calls this.  It drives the same
    RunTracker with a single cell, so a hand-written series can be checked
    against the rules (the tests compare it with the N-cell tracker).
    """
    series = np.asarray(signal, dtype=np.float64).reshape(-1)
    aux_series = {k: np.asarray(v, dtype=np.float64).reshape(-1) for k, v in (aux or {}).items()}
    for key, arr in aux_series.items():
        if arr.size != series.size:
            raise ValueError(f"aux[{key!r}] has {arr.size} frames, signal has {series.size}")
    tracker = RunTracker(1, rule, tolerance)
    found: list[tuple[int, int]] = []
    for t in range(series.size):
        for tr in tracker.update(t, series[t : t + 1], _aux_frame(aux_series, t)):
            found.append((tr.frame, tr.sign))
    if series.size:
        for tr in tracker.finish(series.size - 1):
            found.append((tr.frame, tr.sign))
    return found


def flash_frames_1d(
    signal: np.ndarray,
    rule: Rule = general_rule,
    tolerance: float = RUN_REVERSAL_TOLERANCE["L"],
    aux: dict[str, np.ndarray] | None = None,
) -> list[int]:
    """Frames at which ONE cell completes a flash (transitions paired in order).

    For tests and teaching, like detect_transitions_1d(): the same RunTracker
    and FlashPairer that analyze() uses, seen through a single cell.
    """
    pairer = FlashPairer(1)
    frames: list[int] = []
    for frame, sign in detect_transitions_1d(signal, rule, tolerance, aux):
        if pairer.add(Transition(frame=frame, sign=sign, cells=np.array([0]))).size:
            frames.append(frame)
    return frames
