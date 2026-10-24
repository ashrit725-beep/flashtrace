"""The pipeline: frames in, AnalysisResult out (SPEC section 5).

Per frame (streaming, so the whole capture never has to be in memory):
    uint8 frame -> linear light -> 4x4 cell means -> per-cell signals
        -> RunTracker (general on L; red on ratio+u'v' or on the legacy value)
        -> FlashPairer -> "this cell completed a flash at frame f" masks
        -> heatmap increments.
After the last frame:
    masks -> area scoring (which events cover > 25 % of a window)
        -> sliding one-second counts -> violations, advisories, timeline.

Everything is float64 / int64, in a fixed order, with no randomness and no
threads, so the same frames always give byte-identical JSON.
"""

from __future__ import annotations

from typing import Callable, Iterable, Iterator

import numpy as np

from engine.area import AreaResult, qualifying_events
from engine.cells import cell_signals, frame_to_cells, grid_shape, window_cells, window_px
from engine.constants import CELL_PX, RED_RULES, RUN_REVERSAL_TOLERANCE
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
    if name == "wcag22":
        return red_rule_wcag22, RUN_REVERSAL_TOLERANCE["ratio"]
    return red_rule_legacy, RUN_REVERSAL_TOLERANCE["rv"]


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
    return [
        Advisory(flash_type=flash_type, start=int(s), end=int(e), mean_rate_per_sec=float(rate))
        for s, e, rate in find_advisories(events, fps)
    ]


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
    viewport  (width, height) in CSS px; defaults to the frame size.  The width
              sets the window size (ADR-007).
    red_rule  "wcag22" (default, used for verdicts) or "wcag21-legacy".
    progress  optional callback receiving the number of frames processed so far.
    """
    if red_rule not in RED_RULES:
        raise ValueError(f"unknown red_rule {red_rule!r}; expected one of {RED_RULES}")
    fps = int(fps)
    if fps < 1:
        raise ValueError(f"fps must be >= 1, got {fps}")

    source = _iter_frames(frames)
    try:
        frame = _check_frame(next(source), None)
    except StopIteration:
        raise ValueError("analyze() needs at least 2 frames; got 0") from None
    height, width = int(frame.shape[0]), int(frame.shape[1])
    expected_shape = (height, width, 3)
    if viewport is None:
        viewport_wh = (width, height)
    else:
        viewport_wh = (int(viewport[0]), int(viewport[1]))
        if viewport_wh[0] < 1 or viewport_wh[1] < 1:
            raise ValueError(f"viewport must be positive, got {viewport}")

    cell_px = CELL_PX
    cells_y, cells_x = grid_shape(height, width, cell_px)
    if cells_y == 0 or cells_x == 0:
        raise ValueError(f"frame {height}x{width} is smaller than one {cell_px}x{cell_px} cell")
    n_cells = cells_y * cells_x
    wy, wx = window_cells(viewport_wh[0], cells_y, cells_x, cell_px)
    win_px = window_px(viewport_wh[0])

    red_fn, red_tol = _red_rule_for(red_rule)
    general = _FlashStream(n_cells, general_rule, RUN_REVERSAL_TOLERANCE["L"])
    red = _FlashStream(n_cells, red_fn, red_tol)
    heatmap_flat = np.zeros(n_cells, dtype=np.uint16)

    # ---- streaming pass over the frames -----------------------------------
    t = 0
    while True:
        signals = cell_signals(frame_to_cells(frame, cell_px))
        general.push(t, signals.L, {}, heatmap_flat)
        if red_rule == "wcag22":
            red.push(t, signals.ratio, {"u": signals.u, "v": signals.v}, heatmap_flat)
        else:
            red.push(t, signals.rv, {"ratio": signals.ratio}, heatmap_flat)
        t += 1
        if progress is not None:
            progress(t)
        try:
            frame = _check_frame(next(source), expected_shape)
        except StopIteration:
            break
    frame_count = t
    if frame_count < 2:
        raise ValueError(f"analyze() needs at least 2 frames; got {frame_count}")
    general.finish(frame_count - 1, heatmap_flat)
    red.finish(frame_count - 1, heatmap_flat)

    # ---- area: which events are big enough ---------------------------------
    general_masks = general.stacked(cells_y, cells_x)
    red_masks = red.stacked(cells_y, cells_x)
    general_area = qualifying_events(general_masks, wy, wx)
    red_area = qualifying_events(red_masks, wy, wx)
    general_events = general_area.qualifying
    red_events = red_area.qualifying

    # ---- rate: how many per second ----------------------------------------
    general_max_rate = int(window_counts(general_events, fps).max())
    red_max_rate = int(window_counts(red_events, fps).max())
    violations = sorted(
        _build_violations("general", general_area, fps, cell_px, (wy, wx))
        + _build_violations("red", red_area, fps, cell_px, (wy, wx)),
        key=lambda v: (v.start, 0 if v.type == "general" else 1),
    )
    advisories = _build_advisories("general", general_events, fps) + _build_advisories(
        "red", red_events, fps
    )
    bins = [TimelineBin(t=sec, general_flashes=g, red_flashes=r)
            for sec, g, r in timeline(general_events, red_events, fps)]

    frame_stats = FrameStats(
        general_cells=general_masks.sum(axis=(1, 2)).astype(np.int64),
        red_cells=red_masks.sum(axis=(1, 2)).astype(np.int64),
        general_area_max=general_area.area_max,
        red_area_max=red_area.area_max,
        general_window_y=general_area.window_y,
        general_window_x=general_area.window_x,
        red_window_y=red_area.window_y,
        red_window_x=red_area.window_x,
        general_qualifying=general_events,
        red_qualifying=red_events,
        general_trailing_1s=trailing_counts(general_events, fps),
        red_trailing_1s=trailing_counts(red_events, fps),
    )

    return AnalysisResult(
        frame_count=frame_count,
        fps=fps,
        viewport=viewport_wh,
        cell_px=cell_px,
        grid=(cells_y, cells_x),
        window_cells=(wy, wx),
        window_px=win_px,
        red_rule=red_rule,
        frame_stats=frame_stats,
        violations=violations,
        advisories=advisories,
        heatmap=heatmap_flat.reshape(cells_y, cells_x),
        timeline=bins,
        general_max_rate=general_max_rate,
        red_max_rate=red_max_rate,
        qualifying_general=[int(f) for f in np.flatnonzero(general_events)],
        qualifying_red=[int(f) for f in np.flatnonzero(red_events)],
    )


def evidence_frame_indices(result: AnalysisResult, max_frames: int = 6) -> list[int]:
    """Which still frames to save as evidence: deterministic, at most ``max_frames``.

    Frame 0 (the page at rest), then for each violation its first event frame
    and the frame just before it (the two states of the flash), de-duplicated,
    sorted and capped.
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
