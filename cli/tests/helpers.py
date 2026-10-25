"""Shared helpers for the cli tests: tiny synthetic captures and a stand-in for CaptureMeta.

Frames are 1024x256 so the WCAG window (341x256 px at a 1024 px wide viewport) still fits, while a
whole capture stays small enough to analyse quickly.  Nothing here launches a browser.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import numpy as np

from capture.frames import FrameSequence
from cli.jobs import FRAMES_DIR_NAME, AuditSettings, new_job_dir
from cli.report import build_report
from engine import AnalysisResult, analyze

WIDTH, HEIGHT, FPS = 1024, 256, 60
BLACK = (0, 0, 0)
WHITE = (255, 255, 255)
GREY = (128, 128, 128)

FrameMaker = Callable[[int], np.ndarray]


def solid(colour: tuple[int, int, int]) -> np.ndarray:
    """One uint8 [HEIGHT, WIDTH, 3] frame filled with ``colour``."""
    frame = np.empty((HEIGHT, WIDTH, 3), dtype=np.uint8)
    frame[:, :] = colour
    return frame


def square_wave(hz: int) -> FrameMaker:
    """Full-frame black/white square wave: frame i is white when (i*2*hz // FPS) is odd.

    Integer arithmetic, so frame states are exact (the benchmark uses the same formula, ADR-015).
    At 4 Hz the state changes every 7 or 8 frames and a flash completes every 15 frames.
    """
    black, white = solid(BLACK), solid(WHITE)
    return lambda i: white if (i * 2 * hz // FPS) % 2 else black


def static_grey() -> FrameMaker:
    grey = solid(GREY)
    return lambda i: grey


@dataclass
class AnalysedJob:
    """A job directory whose frames/ holds a synthetic capture, plus the engine's analysis of it."""

    job_dir: Path
    analysis: AnalysisResult
    settings: AuditSettings
    url: str = "file:///synthetic/page.html"


def analysed_job(base: Path, maker: FrameMaker, count: int) -> AnalysedJob:
    """Write ``count`` frames as PNGs into a new job's frames/ and analyse them from disk."""
    settings = AuditSettings(duration_s=count / FPS, viewport=(WIDTH, HEIGHT), fps=FPS)
    job_dir = new_job_dir(base, "file:///synthetic/page.html", settings)
    frames = FrameSequence.new_in_dir(job_dir / FRAMES_DIR_NAME)
    for i in range(count):
        frames.append_array(maker(i))
    analysis = analyze(frames, fps=FPS, viewport=(WIDTH, HEIGHT))
    return AnalysedJob(job_dir=job_dir, analysis=analysis, settings=settings)


@dataclass
class FakeCaptureMeta:
    """Stands in for capture.CaptureMeta: has to_dict() (schema keys + extras) but no to_report_dict()."""

    strategy: str = "waapi_step"
    deterministic: bool = True
    frame_count: int = 120
    uncontrollable: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=lambda: ["synthetic frames", "frame loop took 1.23 s for 120 frames"])
    fallback_reason: str | None = None

    def frame_hashes(self) -> list[str]:
        return [hashlib.sha256(str(i).encode()).hexdigest() for i in range(self.frame_count)]

    def to_dict(self) -> dict[str, Any]:
        hashes = self.frame_hashes()
        return {
            "strategy": self.strategy,
            "deterministic": self.deterministic,
            "frameCount": self.frame_count,
            "hash": hashlib.sha256("".join(hashes).encode()).hexdigest(),
            "frameHashes": hashes,
            "fallbackReason": self.fallback_reason,
            "uncontrollable": list(self.uncontrollable),
            "notes": list(self.notes),
            # Extras that the real to_dict() also has and the schema forbids in a report:
            "viewport": {"width": WIDTH, "height": HEIGHT},
            "fps": FPS,
            "wallTimeS": 1.5,
        }


@dataclass
class FakeCaptureMetaWithReportDict(FakeCaptureMeta):
    """Like FakeCaptureMeta but also has the newer to_report_dict() (schema keys only)."""

    def to_report_dict(self) -> dict[str, Any]:
        data = self.to_dict()
        for extra in ("viewport", "fps", "wallTimeS"):
            data.pop(extra)
        data["notes"] = ["from to_report_dict"]
        return data


def report_for(job: AnalysedJob, meta: FakeCaptureMeta | None = None, **kwargs: Any) -> dict[str, Any]:
    """build_report for a synthetic job with a fixed capturedAt (so reports compare equal)."""
    kwargs.setdefault("captured_at", "2026-10-24T12:00:00Z")
    meta = meta if meta is not None else FakeCaptureMeta(frame_count=job.analysis.frame_count)
    return build_report(job.url, job.settings, meta, job.analysis, job.job_dir, **kwargs)
