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

Step 1 follows each cell's MEAN signal; step 2 can then be asked pixel by
pixel (SPEC 5.4, ADR-002 as amended): the tracker keeps each cell's pixels at
the start and at the extremum of its current run, and when the run ends the
rule is applied to every pixel's two end colours.  A cell that a flashing
element covers only in part then reports exactly the pixels that flashed, and
step 3 pairs each pixel's transitions, so a cell's share of a flash is the
number of its pixels that completed one.
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

# A measure turns uint8 colours [..., 3] (pixels) into the rule's inputs for
# them: (signal [...], aux {name: [...]}), e.g. engine.cells.measure_general.
Measure = Callable[[np.ndarray], tuple[np.ndarray, dict[str, np.ndarray]]]


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
    # bool [len(cells), pixels per cell]: which pixels of each cell made the transition
    # (each row has at least one True).  None means the whole cell (no pixel data).
    pixels: np.ndarray | None = None


def _one_colour(pixels: np.ndarray) -> np.ndarray:
    """uint8 [k, P, 3] -> bool [k]: True where all P pixels of a cell have the same colour."""
    k, p, _ = pixels.shape
    flat = pixels.reshape(k, p * 3)  # each cell's bytes in one row: r, g, b of pixel 0, then pixel 1, ...
    # Every pixel equals the one before it (byte i against byte i - 3) <=> all equal the first.
    return ~np.any(flat[:, 3:] != flat[:, :-3], axis=1)


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
                  since the stamp, i.e. it has arrived,
      pixels    - with a measure: the cell's pixel colours at base and at
                  ext (uint8 [N, P, 3] each).

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

    After the last frame, finish() closes only the runs whose signal has
    settled; a run still moving there has not reached its extremum inside
    the capture and is dropped (SPEC 5.4, ADR-004 as amended).

    Without a measure the rule is applied to the cell's own base and ext
    (signal and aux), and a transition covers whole cells.  With a measure
    (SPEC 5.4, ADR-002 as amended) every frame also brings each cell's pixels
    (uint8 [N, P, 3]); the run boundaries above still come from the cell's
    signal, but when a run ends the rule is applied to each pixel's colour at
    base and at ext, and the cell makes a transition when at least one of
    its pixels does.  Transition.pixels then says which ones did.
    """

    def __init__(self, n_cells: int, rule: Rule, tolerance: float, measure: Measure | None = None) -> None:
        if n_cells < 1:
            raise ValueError(f"n_cells must be >= 1, got {n_cells}")
        if tolerance < 0:
            raise ValueError(f"tolerance must be >= 0, got {tolerance}")
        self.n_cells: int = int(n_cells)
        self.rule: Rule = rule
        self.tolerance: float = float(tolerance)
        self.measure: Measure | None = measure
        self.base_px: np.ndarray | None = None  # uint8 [N, P, 3] once started with a measure
        self.ext_px: np.ndarray | None = None
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

    def _check_pixels(self, pixels: np.ndarray | None) -> np.ndarray | None:
        """With a measure, every frame must bring uint8 pixels [N, P, 3] with the same P; without one, none."""
        if self.measure is None:
            if pixels is not None:
                raise ValueError("pixels given to a RunTracker that has no measure")
            return None
        if pixels is None:
            raise ValueError("a RunTracker with a measure needs the pixels of every frame")
        px = np.asarray(pixels)
        if px.dtype != np.uint8 or px.ndim != 3 or px.shape[0] != self.n_cells or px.shape[2] != 3:
            raise ValueError(f"pixels must be uint8 shaped ({self.n_cells}, P, 3), got {px.dtype} {px.shape}")
        if self.ext_px is not None and px.shape != self.ext_px.shape:
            raise ValueError(f"pixels changed shape from {self.ext_px.shape} to {px.shape}")
        return px

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

    def _start(self, t: int, s: np.ndarray, aux: dict[str, np.ndarray], px: np.ndarray | None) -> None:
        """The first frame is both the base and the extremum of every cell."""
        self.base_val[:] = s
        self.ext_val[:] = s
        self.stamp_val[:] = s
        self.base_frame[:] = t
        self.stamp_frame[:] = t
        self.base_aux = {k: np.array(v, dtype=np.float64) for k, v in sorted(aux.items())}
        self.ext_aux = {k: np.array(v, dtype=np.float64) for k, v in sorted(aux.items())}
        if px is not None:
            self.base_px = px.copy()
            self.ext_px = px.copy()
        self._started = True

    def _set_extremum(
        self, mask: np.ndarray, s: np.ndarray, aux: dict[str, np.ndarray], px: np.ndarray | None
    ) -> None:
        """For the masked cells, the current value (and pixels) become the run's extremum."""
        self.ext_val[mask] = s[mask]
        for key in self.ext_aux:
            self.ext_aux[key][mask] = np.asarray(aux[key], dtype=np.float64)[mask]
        if px is not None:
            np.copyto(self.ext_px, px, where=mask[:, None, None])

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
        if self.base_px is not None:
            np.copyto(self.base_px, self.ext_px, where=mask[:, None, None])

    def _rule_on_colours(self, colours_a: np.ndarray, colours_b: np.ndarray) -> np.ndarray:
        """The rule applied to each pair of uint8 colours a -> b ([..., 3] each): bool [...]."""
        sig_a, aux_a = self.measure(colours_a)
        sig_b, aux_b = self.measure(colours_b)
        return np.asarray(self.rule(sig_a, sig_b, aux_a, aux_b), dtype=bool)

    def _pixel_hits(self, idx: np.ndarray) -> np.ndarray:
        """bool [len(idx), P]: which pixels of the cells idx made a transition from base to ext.

        Most cells are one flat colour at both ends of a run; for those the
        rule is asked once and the answer holds for all their pixels.
        """
        base_px, ext_px = np.take(self.base_px, idx, axis=0), np.take(self.ext_px, idx, axis=0)
        hits = np.zeros(base_px.shape[:2], dtype=bool)
        flat = _one_colour(base_px) & _one_colour(ext_px)
        if flat.any():
            hits[flat] = self._rule_on_colours(base_px[flat, :1], ext_px[flat, :1])  # [k, 1] fills [k, P]
        mixed = ~flat
        if mixed.any():
            hits[mixed] = self._rule_on_colours(base_px[mixed], ext_px[mixed])
        return hits

    def _close_runs(self, mask: np.ndarray, sign: int) -> list[Transition]:
        """Evaluate the rule on base -> ext for the masked cells; group hits by frame.

        Without a measure the rule sees each cell's own signal and aux; with
        one it sees every pixel, and a cell is a hit when any pixel is.
        """
        idx = np.flatnonzero(mask)
        if idx.size == 0:
            return []
        if self.measure is None:
            base_aux = {k: v[idx] for k, v in self.base_aux.items()}
            ext_aux = {k: v[idx] for k, v in self.ext_aux.items()}
            passed = np.asarray(
                self.rule(self.base_val[idx], self.ext_val[idx], base_aux, ext_aux), dtype=bool
            )
            pixel_hits = None
        else:
            pixel_hits = self._pixel_hits(idx)
            passed = pixel_hits.any(axis=1)
            pixel_hits = pixel_hits[passed]
        hits = idx[passed]
        if hits.size == 0:
            return []
        # Different cells may have reached their extremum on different frames;
        # one Transition per distinct frame.  A stable sort keeps cell ids
        # ascending inside each group, so the output order is fully determined.
        frames = self.stamp_frame[hits]
        order = np.argsort(frames, kind="stable")
        hits, frames = hits[order], frames[order]
        if pixel_hits is not None:
            pixel_hits = pixel_hits[order]
        unique_frames, starts, counts = np.unique(frames, return_index=True, return_counts=True)
        return [
            Transition(frame=int(f), sign=sign, cells=hits[st : st + c],
                       pixels=None if pixel_hits is None else pixel_hits[st : st + c])
            for f, st, c in zip(unique_frames.tolist(), starts.tolist(), counts.tolist())
        ]

    # -- public API ---------------------------------------------------------

    def update(
        self, t: int, signal: np.ndarray, aux: dict[str, np.ndarray], pixels: np.ndarray | None = None
    ) -> list[Transition]:
        """Feed frame ``t`` (and, with a measure, its pixels [N, P, 3]).  Returns the transitions whose runs ended on it."""
        s = self._check(t, signal, aux)
        px = self._check_pixels(pixels)
        self._last_t = int(t)
        if not self._started:
            self._start(t, s, aux, px)
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
        self._set_extremum(new_run | extend, s, aux, px)
        self._move_stamps(t, s, new_run, extend, big_step, paused)
        return emitted

    def finish(self, t_last: int) -> list[Transition]:
        """Close the runs still open after the last frame whose signal has arrived (SPEC 5.4, ADR-004 as amended).

        A run whose signal has settled (stood still or turned back by no more
        than the tolerance since its stamp) has reached its extremum inside
        the capture: it is evaluated and timestamped at its stamp frame,
        exactly as in update().  A run still moving at the last frame has not
        reached its extremum yet - a fade cut off by the end of the capture,
        or a step shown by the last frame alone.  Its real end lies after the
        capture, so it is dropped: stamping it at the last frame would put a
        flash up to a few frames EARLIER than it happens and can push a
        smooth 3 Hz pulse to 4 flashes in the last second.  ``t_last`` is
        checked to be the last frame fed in.
        """
        if self._finished:
            raise ValueError("RunTracker.finish() called twice")
        if not self._started:
            self._finished = True
            return []
        if t_last != self._last_t:
            raise ValueError(f"finish({t_last}) does not match the last frame fed ({self._last_t})")
        arrived = self.settled
        emitted = (self._close_runs((self.direction == 1) & arrived, +1)
                   + self._close_runs((self.direction == -1) & arrived, -1))
        self.direction[:] = 0
        self._finished = True
        return emitted


class FlashPairer:
    """Pair each cell's transitions into flashes (SPEC 5.5, ADR-005, ADR-018).

    WCAG 2.2 glossary: a flash is "a pair of opposing changes".  Transitions
    are paired in order, non-overlapping (1st+2nd, 3rd+4th, ...), and only
    opposing transitions pair.  A pending transition is remembered by its
    sign; when the opposite sign arrives the pair is complete and the flash
    is counted at that frame.  Two same-sign transitions in a row are
    possible when a run below the threshold sits between them (rise, small
    dip, rise); the newer one then replaces the pending one (ADR-018), so a
    flash is always one rise and one fall.

    The pairing is kept per pixel (SPEC 5.5, ADR-002 as amended): each cell
    has ``pixels_per_cell`` pending signs, Transition.pixels says which of
    them a transition reaches, and a cell's share of a completed flash is
    the number of its pixels that complete one.  A transition without pixel
    data reaches every pixel of its cells; with pixels_per_cell = 1 a cell
    is one pixel, which is the whole-cell pairing.
    """

    def __init__(self, n_cells: int, pixels_per_cell: int = 1) -> None:
        if n_cells < 1:
            raise ValueError(f"n_cells must be >= 1, got {n_cells}")
        if pixels_per_cell < 1:
            raise ValueError(f"pixels_per_cell must be >= 1, got {pixels_per_cell}")
        self.n_cells: int = int(n_cells)
        self.pixels_per_cell: int = int(pixels_per_cell)
        self.pending_sign: np.ndarray = np.zeros((self.n_cells, self.pixels_per_cell), dtype=np.int8)

    def _pixels_reached(self, transition: Transition, n_cells: int) -> np.ndarray:
        """bool [n_cells, pixels_per_cell]: the pixels this transition reaches."""
        if transition.pixels is None:
            return np.ones((n_cells, self.pixels_per_cell), dtype=bool)
        reached = np.asarray(transition.pixels, dtype=bool)
        if reached.shape != (n_cells, self.pixels_per_cell):
            raise ValueError(f"pixels must be shaped ({n_cells}, {self.pixels_per_cell}), got {reached.shape}")
        return reached

    def complete(self, transition: Transition) -> tuple[np.ndarray, np.ndarray]:
        """Record a transition; return (cells whose flash it COMPLETES, how many pixels of each).

        Only cells where at least one pixel completes a flash are returned.
        """
        cells = np.asarray(transition.cells, dtype=np.intp)
        if transition.sign not in (1, -1):
            raise ValueError(f"sign must be +1 or -1, got {transition.sign}")
        reached = self._pixels_reached(transition, cells.size)
        pending = self.pending_sign[cells]
        completes = reached & (pending == -transition.sign)
        # A completed pair is used up (non-overlapping pairing); every other pixel the transition
        # reached now waits with its sign, replacing an older same-sign transition (ADR-018).
        self.pending_sign[cells] = np.where(completes, 0, np.where(reached, transition.sign, pending))
        counts = completes.sum(axis=1)
        done = counts > 0
        return cells[done], counts[done]

    def add(self, transition: Transition) -> np.ndarray:
        """Record a transition; return the cells whose flash it COMPLETES (in at least one pixel)."""
        return self.complete(transition)[0]


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
