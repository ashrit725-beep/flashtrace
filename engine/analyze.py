"""The pipeline: frames in, AnalysisResult out (SPEC section 5).

Per frame (streaming: a frame is dropped as soon as its signals are read):
    uint8 frame -> linear light -> 4x4 cell means -> per-cell signals
        -> RunTracker (general on L; red on ratio+u'v' or on the legacy value)
        -> FlashPairer -> "this cell completed a flash at frame f" masks
        -> heatmap increments.
After the last frame:
    masks -> area scoring (which events cover > 25 % of a window)
        -> sliding one-second counts -> violations, advisories, timeline.

What stays in memory for the whole capture is one true/false per cell per
frame for each flash type (the masks, needed by the area step) plus a few
numbers per cell for the trackers; the frames themselves are never kept.

Everything is float64 / int64, in a fixed order, with no randomness and no
threads, so the same frames always give byte-identical JSON.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable, Iterator

import numpy as np

from engine.area import AreaResult, qualifying_events
from engine.cells import (
    cell_signals,
    frame_to_cells,
    grid_shape,
    window_cells,
    window_cells_full,
    window_px,
)
from engine.constants import CELL_PX, EVENT_FRAME_TOLERANCE, RED_RULES, RUN_REVERSAL_TOLERANCE
from engine.rate import find_advisories, find_violations, timeline, trailing_counts, window_counts
from engine.transitions import (
    FlashPairer,
    Rule,
    RunTracker,
    Transition,
    general_rule,
    red_rule_legacy,
    red_rule_wcag22,
)
from engine.types import (
    Advisory,
    AnalysisResult,
    FrameStats,
    Rect,
    TimelineBin,
    Violation,
)

FrameSource = np.ndarray | Iterable[np.ndarray]
ProgressCallback = Callable[[int], None]


def _iter_frames(frames: FrameSource) -> Iterator[np.ndarray]:
    """Accept a [T, H, W, 3] array or any iterable of [H, W, 3] frames."""
    if isinstance(frames, np.ndarray):
        if frames.ndim != 4 or frames.shape[3] != 3:
            raise ValueError(f"frames array must be shaped [T, H, W, 3], got {frames.shape}")
        if frames.dtype != np.uint8:
            raise ValueError(f"frames must be uint8, got {frames.dtype}")
        return iter(frames)
    return iter(frames)


def _check_frame(frame: object, expected: tuple[int, int, int] | None) -> np.ndarray:
    """Every frame must be uint8 [H, W, 3] and the same size as the first one."""
    arr = np.asarray(frame)
    if arr.dtype != np.uint8:
        raise ValueError(f"every frame must be uint8, got {arr.dtype}")
    if arr.ndim != 3 or arr.shape[2] != 3:
        raise ValueError(f"every frame must be shaped [H, W, 3], got {arr.shape}")
    if expected is not None and arr.shape != expected:
        raise ValueError(f"frame shape changed from {expected} to {arr.shape}")
    return arr


class _FlashStream:
    """One flash type's tracker + pairer + per-frame masks, fed one frame at a time."""

    def __init__(self, n_cells: int, rule: Rule, tolerance: float) -> None:
        self.n_cells = n_cells
        self.tracker = RunTracker(n_cells, rule, tolerance)
        self.pairer = FlashPairer(n_cells)
        # One bool[N] per frame, created as frames arrive.  Transitions are
        # timestamped in the past (at the run's extremum), so the mask for
        # that earlier frame already exists and can still be written.
        self.masks: list[np.ndarray] = []

    def push(self, t: int, signal: np.ndarray, aux: dict[str, np.ndarray], heatmap: np.ndarray) -> None:
        self.masks.append(np.zeros(self.n_cells, dtype=bool))
        self._absorb(self.tracker.update(t, signal, aux), heatmap)

    def finish(self, t_last: int, heatmap: np.ndarray) -> None:
        self._absorb(self.tracker.finish(t_last), heatmap)

    def _absorb(self, transitions: list[Transition], heatmap: np.ndarray) -> None:
        for tr in transitions:
            completed = self.pairer.add(tr)
            if completed.size:
                self.masks[tr.frame][completed] = True
                heatmap[completed] += 1  # indices are unique within one transition

    def stacked(self, cells_y: int, cells_x: int) -> np.ndarray:
        """All masks as bool [T, cells_y, cells_x]."""
        return np.stack(self.masks, axis=0).reshape(len(self.masks), cells_y, cells_x)


def _red_rule_for(name: str) -> tuple[Rule, float]:
    """The red transition rule and its reversal tolerance for a red_rule name."""
    if name == "wcag22":
        return red_rule_wcag22, RUN_REVERSAL_TOLERANCE["ratio"]
    return red_rule_legacy, RUN_REVERSAL_TOLERANCE["rv"]


@dataclass(frozen=True)
class _Geometry:
    """Grid and window sizes for one capture, all counted in cells."""

    cells_y: int
    cells_x: int
    wy: int                  # rows of the window slid over the grid (clamped to it)
    wx: int                  # columns of that window
    full_window_cells: int   # size of the unclamped window: the area denominator (ADR-019)


@dataclass
class _TypeResult:
    """Everything measured for one flash type (general or red) after the last frame."""

    masks: np.ndarray            # bool [T, cells_y, cells_x]: cells completing a flash at t
    area: AreaResult
    violations: list[Violation]
    advisories: list[Advisory]
    max_rate: int                # highest count in any one-second window


def _check_arguments(fps: int, red_rule: str) -> int:
    """Reject an unknown red rule or a frame rate below 1; return fps as an int."""
    if red_rule not in RED_RULES:
        raise ValueError(f"unknown red_rule {red_rule!r}; expected one of {RED_RULES}")
    fps = int(fps)
    if fps < 1:
        raise ValueError(f"fps must be >= 1, got {fps}")
    return fps


def _resolve_viewport(viewport: tuple[int, int] | None, width: int, height: int) -> tuple[int, int]:
    """The (width, height) in CSS px to analyse with: the given viewport, or the frame size.

    SPEC 5.1: frames are taken at device scale factor 1, so the frame width
    must equal the viewport width; the window size is derived from it.
    """
    if viewport is None:
        return width, height
    viewport_wh = (int(viewport[0]), int(viewport[1]))
    if viewport_wh[0] < 1 or viewport_wh[1] < 1:
        raise ValueError(f"viewport must be positive, got {viewport}")
    if viewport_wh[0] != width:
        raise ValueError(
            f"frame width {width} px differs from viewport width {viewport_wh[0]} px; "
            "SPEC 5.1 needs frames at device scale factor 1"
        )
    return viewport_wh


def _geometry(height: int, width: int, viewport_width: int) -> _Geometry:
    """Cell grid of the frame plus the scanned and the full window (SPEC 5.2, 5.6)."""
    cells_y, cells_x = grid_shape(height, width, CELL_PX)
    if cells_y == 0 or cells_x == 0:
        raise ValueError(f"frame {height}x{width} is smaller than one {CELL_PX}x{CELL_PX} cell")
    wy, wx = window_cells(viewport_width, cells_y, cells_x, CELL_PX)
    full_y, full_x = window_cells_full(viewport_width, CELL_PX)
    return _Geometry(cells_y, cells_x, wy, wx, full_y * full_x)


def _next_frame(source: Iterator[np.ndarray], expected: tuple[int, int, int] | None) -> np.ndarray | None:
    """The next checked frame from the source, or None when the source is used up."""
    try:
        return _check_frame(next(source), expected)
    except StopIteration:
        return None


def _push_frame(
    t: int, frame: np.ndarray, red_rule: str, general: _FlashStream, red: _FlashStream, heatmap: np.ndarray
) -> None:
    """Read one frame's cell signals and feed them to both flash streams (SPEC 5.2-5.5)."""
    signals = cell_signals(frame_to_cells(frame, CELL_PX))
    general.push(t, signals.L, {}, heatmap)
    if red_rule == "wcag22":
        red.push(t, signals.ratio, {"u": signals.u, "v": signals.v}, heatmap)
    else:
        red.push(t, signals.rv, {"ratio": signals.ratio}, heatmap)


def _stream(
    first: np.ndarray,
    source: Iterator[np.ndarray],
    n_cells: int,
    red_rule: str,
    progress: ProgressCallback | None,
) -> tuple[_FlashStream, _FlashStream, np.ndarray, int]:
    """Run every frame through the trackers, one frame at a time.

    In: the first frame (already checked), the rest of the frames, the
    number of cells, the red rule name and the optional progress callback.
    Out: the general and red streams (holding their per-frame masks), the
    flat uint16 heatmap and the number of frames seen.
    """
    red_fn, red_tol = _red_rule_for(red_rule)
    general = _FlashStream(n_cells, general_rule, RUN_REVERSAL_TOLERANCE["L"])
    red = _FlashStream(n_cells, red_fn, red_tol)
    heatmap_flat = np.zeros(n_cells, dtype=np.uint16)
    expected_shape = (int(first.shape[0]), int(first.shape[1]), 3)  # every frame must match the first
    t = 0
    frame: np.ndarray | None = first
    while frame is not None:
        _push_frame(t, frame, red_rule, general, red, heatmap_flat)
        t += 1
        if progress is not None:
            progress(t)
        frame = _next_frame(source, expected_shape)
    if t < 2:
        raise ValueError(f"analyze() needs at least 2 frames; got {t}")
    # Runs still open after the last frame are closed there (SPEC 5.4).
    general.finish(t - 1, heatmap_flat)
    red.finish(t - 1, heatmap_flat)
    return general, red, heatmap_flat, t


def _build_violations(
    flash_type: str,
    area: AreaResult,
    fps: int,
    cell_px: int,
    window_yx: tuple[int, int],
) -> list[Violation]:
    """Violation records for one flash type (SPEC 5.7)."""
    events = area.qualifying
    event_frames = np.flatnonzero(events)
    wy, wx = window_yx
    violations: list[Violation] = []
    for start, end, max_count in find_violations(events, fps):
        inside = event_frames[(event_frames >= start) & (event_frames <= end)]
        if inside.size:
            # The window to show: the argmax window of the largest event in
            # the span (np.argmax picks the earliest on ties).
            best = int(inside[np.argmax(area.area_max[inside])])
            rect = Rect(
                x=float(area.window_x[best] * cell_px),
                y=float(area.window_y[best] * cell_px),
                w=float(wx * cell_px),
                h=float(wy * cell_px),
            )
        else:  # cannot happen (a failing window holds >= 4 events) but stay total
            rect = Rect(0.0, 0.0, float(wx * cell_px), float(wy * cell_px))
        violations.append(
            Violation(
                start=int(start),
                end=int(end),
                type=flash_type,
                max_rate_per_sec=int(max_count),
                window=rect,
                frame_indices=[int(f) for f in inside],
            )
        )
    return violations


def _build_advisories(flash_type: str, events: np.ndarray, fps: int) -> list[Advisory]:
    """Advisory records for one flash type (SPEC 5.9; not a WCAG rule, ADR-009)."""
    return [
        Advisory(flash_type=flash_type, start=int(s), end=int(e), mean_rate_per_sec=float(rate))
        for s, e, rate in find_advisories(events, fps)
    ]


def _score_type(flash_type: str, stream: _FlashStream, geo: _Geometry, fps: int) -> _TypeResult:
    """Area and rate for one flash type (SPEC 5.6, 5.7, 5.9).

    In: the type name, its stream after the last frame, the geometry and fps.
    Out: its masks, area scoring, violations, advisories and maximum rate.
    """
    masks = stream.stacked(geo.cells_y, geo.cells_x)
    area = qualifying_events(masks, geo.wy, geo.wx, full_window_cells=geo.full_window_cells)
    return _TypeResult(
        masks=masks,
        area=area,
        violations=_build_violations(flash_type, area, fps, CELL_PX, (geo.wy, geo.wx)),
        advisories=_build_advisories(flash_type, area.qualifying, fps),
        max_rate=int(window_counts(area.qualifying, fps).max()),
    )


def _frame_stats(general: _TypeResult, red: _TypeResult, fps: int) -> FrameStats:
    """Per-frame statistics of both flash types (SPEC 5.8)."""
    return FrameStats(
        general_cells=general.masks.sum(axis=(1, 2)).astype(np.int64),
        red_cells=red.masks.sum(axis=(1, 2)).astype(np.int64),
        general_area_max=general.area.area_max,
        red_area_max=red.area.area_max,
        general_window_y=general.area.window_y,
        general_window_x=general.area.window_x,
        red_window_y=red.area.window_y,
        red_window_x=red.area.window_x,
        general_qualifying=general.area.qualifying,
        red_qualifying=red.area.qualifying,
        general_trailing_1s=trailing_counts(general.area.qualifying, fps),
        red_trailing_1s=trailing_counts(red.area.qualifying, fps),
    )


def _violation_cells(violation: Violation, masks: np.ndarray) -> np.ndarray:
    """Cells that flashed in one violation's events: the union of E[t] over its event frames.

    In: the violation and the bool masks [T, cells_y, cells_x] of its flash type.
    Out: bool [cells_y, cells_x].  E[t] = M[t-1] | M[t] (SPEC 5.6 step 1, ADR-019), so each
    event frame also brings in the completions of the frame before it.
    """
    cells = np.zeros(masks.shape[1:], dtype=bool)
    for t in violation.frame_indices:
        first = max(0, t - EVENT_FRAME_TOLERANCE)  # frames before frame 0 count as empty
        cells |= masks[first : t + 1].any(axis=0)
    return cells


def _assemble(
    general: _TypeResult,
    red: _TypeResult,
    geo: _Geometry,
    heatmap_flat: np.ndarray,
    frame_count: int,
    fps: int,
    viewport_wh: tuple[int, int],
    red_rule: str,
) -> AnalysisResult:
    """Put both flash types' results into one AnalysisResult (SPEC 5.8)."""
    # Violations of both types in time order; general first when they start together.
    violations = sorted(
        general.violations + red.violations,
        key=lambda v: (v.start, 0 if v.type == "general" else 1),
    )
    bins = [TimelineBin(t=sec, general_flashes=g, red_flashes=r)
            for sec, g, r in timeline(general.area.qualifying, red.area.qualifying, fps)]
    masks_by_type = {"general": general.masks, "red": red.masks}
    violation_cells = [_violation_cells(v, masks_by_type[v.type]) for v in violations]
    return AnalysisResult(
        frame_count=frame_count,
        fps=fps,
        viewport=viewport_wh,
        cell_px=CELL_PX,
        grid=(geo.cells_y, geo.cells_x),
        window_cells=(geo.wy, geo.wx),
        window_px=window_px(viewport_wh[0]),
        red_rule=red_rule,
        frame_stats=_frame_stats(general, red, fps),
        violations=violations,
        advisories=general.advisories + red.advisories,
        heatmap=heatmap_flat.reshape(geo.cells_y, geo.cells_x),
        timeline=bins,
        general_max_rate=general.max_rate,
        red_max_rate=red.max_rate,
        qualifying_general=[int(f) for f in np.flatnonzero(general.area.qualifying)],
        qualifying_red=[int(f) for f in np.flatnonzero(red.area.qualifying)],
        violation_cells=violation_cells,
    )


def analyze(
    frames: FrameSource,
    fps: int = 60,
    viewport: tuple[int, int] | None = None,
    red_rule: str = "wcag22",
    progress: ProgressCallback | None = None,
) -> AnalysisResult:
    """Analyse a frame sequence against WCAG 2.2 SC 2.3.1.

    frames    uint8 [T, H, W, 3] array, or any iterable yielding uint8 [H, W, 3]
              frames (a generator is fine: frames are processed one at a time).
    fps       frames per second of animation time; frame i is at i / fps s.
    viewport  (width, height) in CSS px; defaults to the frame size.  Its width
              must equal the frame width (SPEC 5.1) and sets the window size (ADR-007).
    red_rule  "wcag22" (default, used for verdicts) or "wcag21-legacy".
    progress  optional callback receiving the number of frames processed so far.
    """
    fps = _check_arguments(fps, red_rule)
    source = _iter_frames(frames)
    first = _next_frame(source, None)
    if first is None:
        raise ValueError("analyze() needs at least 2 frames; got 0")
    height, width = int(first.shape[0]), int(first.shape[1])
    viewport_wh = _resolve_viewport(viewport, width, height)
    geo = _geometry(height, width, viewport_wh[0])
    general_stream, red_stream, heatmap_flat, frame_count = _stream(
        first, source, geo.cells_y * geo.cells_x, red_rule, progress
    )
    general = _score_type("general", general_stream, geo, fps)
    red = _score_type("red", red_stream, geo, fps)
    return _assemble(general, red, geo, heatmap_flat, frame_count, fps, viewport_wh, red_rule)


def evidence_frame_indices(result: AnalysisResult, max_frames: int = 6) -> list[int]:
    """Which still frames to save as evidence: deterministic, at most ``max_frames``.

    Frame 0 (the page at rest), then for each violation its first event frame
    and the frame just before it, de-duplicated, sorted and capped.  An event
    is counted on the frame where its flash completes (ADR-019), so for a
    flash whose cells complete together the two frames show the state just
    before and just after the completing transition.
    """
    if max_frames < 1:
        return []
    picks: set[int] = {0}
    for violation in result.violations:
        first = violation.frame_indices[0] if violation.frame_indices else violation.start
        picks.add(int(first))
        if first - 1 >= 0:
            picks.add(int(first) - 1)
    picks = {f for f in picks if 0 <= f < result.frame_count}
    return sorted(picks)[:max_frames]
