"""Result types: what analyze() returns and how it becomes report JSON (SPEC 5.8, 8).

Every ``to_dict()`` produces only ints, floats, strings, lists and dicts in a
fixed key order, with the camelCase names used by engine/schema/report.schema.json,
so ``json.dumps`` of the same analysis is byte-identical every time.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field

import numpy as np

from engine.constants import ENGINE_VERSION, MAX_FLASHES_PER_SECOND

PASS_REASON: str = "No 2.3.1 violation detected under the tested conditions."
ADVISORY_LABEL: str = "advisory - not a WCAG 2.3.1 criterion"


@dataclass
class Rect:
    """A rectangle in CSS pixels (x, y = top-left corner)."""

    x: float
    y: float
    w: float
    h: float

    def to_dict(self) -> dict[str, float]:
        return {"x": float(self.x), "y": float(self.y), "w": float(self.w), "h": float(self.h)}


@dataclass
class Violation:
    """A span of frames in which one flash type exceeded three per second."""

    start: int                 # first frame of the first failing one-second window
    end: int                   # last frame of the last failing one-second window
    type: str                  # "general" or "red"
    max_rate_per_sec: int      # highest one-second count inside the span
    window: Rect               # the scored window of the largest event, in CSS px, made of whole
                               # cells (85x64 cells = 340x256 px at 1024 px wide; windowPx holds
                               # the nominal 341x256, ADR-006; clipped to a short viewport, ADR-019)
    frame_indices: list[int]   # frames of the qualifying events inside the span

    def to_dict(self) -> dict[str, object]:
        return {
            "start": int(self.start),
            "end": int(self.end),
            "type": str(self.type),
            "maxRatePerSec": int(self.max_rate_per_sec),
            "window": self.window.to_dict(),
            "frameIndices": [int(f) for f in self.frame_indices],
        }


@dataclass
class Advisory:
    """Sustained 2+ flashes/s for more than 5 s.  Informational; never a verdict."""

    flash_type: str            # "general" or "red"
    start: int
    end: int
    mean_rate_per_sec: float
    type: str = "sustained-flashing"
    label: str = ADVISORY_LABEL

    def to_dict(self) -> dict[str, object]:
        return {
            "type": str(self.type),
            "flashType": str(self.flash_type),
            "start": int(self.start),
            "end": int(self.end),
            "meanRatePerSec": float(self.mean_rate_per_sec),
            "label": str(self.label),
        }


@dataclass
class TimelineBin:
    """Qualifying events in the second starting at ``t`` seconds.

    Bin k covers frames [k*fps, (k+1)*fps).  The last bin may cover only part
    of a second when the capture ends early; it holds its raw count, so the
    bins always add up to the event totals (ADR-021).
    """

    t: float
    general_flashes: int
    red_flashes: int

    def to_dict(self) -> dict[str, object]:
        return {
            "t": float(self.t),
            "generalFlashes": int(self.general_flashes),
            "redFlashes": int(self.red_flashes),
        }


@dataclass
class FrameStats:
    """Per-frame numbers, one entry per frame in every array (length T)."""

    general_cells: np.ndarray        # int64: cells completing a general flash at t
    red_cells: np.ndarray            # int64: cells completing a red flash at t
    general_area_max: np.ndarray     # float64: best window's share of the full window, E[t] = M[t-1] | M[t]
    red_area_max: np.ndarray         # float64
    general_window_y: np.ndarray     # int64: top-left cell of that best window
    general_window_x: np.ndarray     # int64
    red_window_y: np.ndarray         # int64
    red_window_x: np.ndarray         # int64
    general_qualifying: np.ndarray   # bool: a qualifying general event is counted at t (ADR-019)
    red_qualifying: np.ndarray       # bool
    general_trailing_1s: np.ndarray  # int64: qualifying general events in [t-fps+1, t]
    red_trailing_1s: np.ndarray      # int64

    def __len__(self) -> int:
        return int(self.general_cells.shape[0])

    def row(self, t: int) -> dict[str, object]:
        """One frame's statistics as a JSON-safe dict with camelCase keys."""
        return {
            "frame": int(t),
            "generalCells": int(self.general_cells[t]),
            "redCells": int(self.red_cells[t]),
            "generalAreaMax": float(self.general_area_max[t]),
            "redAreaMax": float(self.red_area_max[t]),
            "generalWindow": {"x": int(self.general_window_x[t]), "y": int(self.general_window_y[t])},
            "redWindow": {"x": int(self.red_window_x[t]), "y": int(self.red_window_y[t])},
            "generalQualifying": bool(self.general_qualifying[t]),
            "redQualifying": bool(self.red_qualifying[t]),
            "generalTrailing1s": int(self.general_trailing_1s[t]),
            "redTrailing1s": int(self.red_trailing_1s[t]),
        }


def heatmap_to_base64(heatmap: np.ndarray) -> str:
    """uint16 [h, w] -> base64 of little-endian uint16 bytes, row-major."""
    arr = np.asarray(heatmap)
    if arr.ndim != 2:
        raise ValueError(f"heatmap must be 2-D, got shape {arr.shape}")
    # Fix the byte order explicitly so the string is the same on every machine.
    little_endian = np.ascontiguousarray(arr, dtype="<u2")
    return base64.b64encode(little_endian.tobytes(order="C")).decode("ascii")


def heatmap_from_base64(s: str, w: int, h: int) -> np.ndarray:
    """Reverse of heatmap_to_base64 -> writable native uint16 [h, w]."""
    raw = base64.b64decode(s)
    flat = np.frombuffer(raw, dtype="<u2")
    if flat.size != w * h:
        raise ValueError(f"heatmap data holds {flat.size} cells, expected {w}x{h}={w * h}")
    return flat.reshape(h, w).astype(np.uint16)


@dataclass
class AnalysisResult:
    """Everything the engine found for one capture."""

    frame_count: int
    fps: int
    viewport: tuple[int, int]        # (width, height) in CSS px
    cell_px: int
    grid: tuple[int, int]            # (cells_y, cells_x)
    window_cells: tuple[int, int]    # (wy, wx) of the scanned window, clamped to the grid
    window_px: tuple[int, int]       # (w, h) in CSS px
    red_rule: str
    frame_stats: FrameStats
    violations: list[Violation]
    advisories: list[Advisory]
    heatmap: np.ndarray              # uint16 [cells_y, cells_x]: completed flashes of either type
    timeline: list[TimelineBin]
    general_max_rate: int
    red_max_rate: int
    qualifying_general: list[int] = field(default_factory=list)  # frames of qualifying general events
    qualifying_red: list[int] = field(default_factory=list)      # frames of qualifying red events

    @property
    def verdict(self) -> str:
        """"FAIL" when any one-second window exceeded the limit, otherwise "PASS"."""
        return "FAIL" if self.violations else "PASS"

    def reason(self) -> str:
        """One plain-English sentence explaining the verdict."""
        if not self.violations:
            return PASS_REASON
        # Describe the worst violation: highest rate, then earliest, then general.
        worst = max(
            self.violations,
            key=lambda v: (v.max_rate_per_sec, -v.start, 1 if v.type == "general" else 0),
        )
        other = "red" if worst.type == "general" else "general"
        other_failed = any(v.type == other for v in self.violations)
        label = "General" if worst.type == "general" else "Red"
        also = f" ({other} flashes also exceeded the limit)" if other_failed else ""
        return (
            f"{label} flashes reached {worst.max_rate_per_sec} per second in a "
            f"{self.window_px[0]}x{self.window_px[1]} px window at frames "
            f"{worst.start}-{worst.end}{also}; WCAG 2.3.1 allows at most {MAX_FLASHES_PER_SECOND}."
        )

    def to_dict(self) -> dict[str, object]:
        """JSON-safe dict with the report's field names, in a fixed key order."""
        cells_y, cells_x = self.grid
        return {
            "verdict": self.verdict,
            "reason": self.reason(),
            "violations": [v.to_dict() for v in self.violations],
            "advisory": [a.to_dict() for a in self.advisories],
            "timeline": [b.to_dict() for b in self.timeline],
            "heatmap": {
                "cellSize": int(self.cell_px),
                "w": int(cells_x),
                "h": int(cells_y),
                "data": heatmap_to_base64(self.heatmap),
            },
            "generalMaxRatePerSec": int(self.general_max_rate),
            "redMaxRatePerSec": int(self.red_max_rate),
            "qualifyingGeneralFrames": [int(f) for f in self.qualifying_general],
            "qualifyingRedFrames": [int(f) for f in self.qualifying_red],
            "windowPx": {"width": int(self.window_px[0]), "height": int(self.window_px[1])},
            "grid": {"w": int(cells_x), "h": int(cells_y)},
            "redRule": str(self.red_rule),
            "engineVersion": ENGINE_VERSION,
            "frameCount": int(self.frame_count),
            "fps": int(self.fps),
        }
