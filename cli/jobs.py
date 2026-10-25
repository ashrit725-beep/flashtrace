"""Job directories: where one audit keeps its frames, evidence and reports.

Layout (used by the command-line tool now and by the local server later)::

    <base>/jobs/<job_id>/
        frames/       PNG frames written by capture (000000.png, 000001.png, ...)
        evidence/     copies of the few frames the report shows as evidence
        report.json   the report, validated against engine/schema/report.schema.json
        report.html   the same report as one self-contained HTML page
        meta.json     the job's settings and status ("running", "done" or "error")

``base`` defaults to ``<current directory>/.flashtrace``.

A job id is ``YYYYMMDD-HHMMSS-<6 hex>``: the UTC start time, then the first six hex digits of
sha256(url + settings).  Two audits of the same page with the same settings that start in the same
second get a ``-1``, ``-2``, ... suffix so that neither overwrites the other.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_BASE_NAME = ".flashtrace"
JOBS_DIR_NAME = "jobs"
FRAMES_DIR_NAME = "frames"
EVIDENCE_DIR_NAME = "evidence"
REPORT_JSON_NAME = "report.json"
REPORT_HTML_NAME = "report.html"
META_JSON_NAME = "meta.json"

# Eight date digits, six time digits, six hex digits, then an optional "-N" collision suffix.
# Anything else is rejected, so a job id can never contain "/" or ".." (the server will take ids
# from URLs).
JOB_ID_PATTERN = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{6}(?:-\d{1,3})?$")
MAX_SAME_SECOND_JOBS = 1000

# The allowed values of each choice option.  STRATEGY_CHOICES mirrors capture.strategies.FALLBACK_ORDER
# plus "auto" (a test checks that they agree); it is copied here so that this module does not have to
# import Playwright.
RED_RULE_CHOICES: tuple[str, ...] = ("wcag22", "wcag21-legacy")
ATTRIBUTE_CHOICES: tuple[str, ...] = ("none", "bbox", "ablate")
STRATEGY_CHOICES: tuple[str, ...] = ("auto", "waapi_step", "virtual_time", "realtime")

# Capture records round(duration x fps) frames.  A request that rounds to zero frames asks for no capture
# at all, so it is rejected as a bad option.  One frame is still accepted: SPEC 6.4 already makes "fewer
# than 2*fps frames" an INCONCLUSIVE verdict with a reason.
MIN_CAPTURE_FRAMES = 1


@dataclass(frozen=True)
class AuditSettings:
    """Everything the user chose that can change the result of an audit."""

    duration_s: float = 10.0
    viewport: tuple[int, int] = (1024, 768)
    fps: int = 60
    red_rule: str = "wcag22"
    attribute: str = "none"
    strategy: str = "auto"
    script: str | None = None  # path of the interaction script, exactly as the user gave it

    def problems(self) -> list[str]:
        """Plain-English reasons these settings cannot be used (empty when they are fine)."""
        found: list[str] = []
        if not (math.isfinite(self.duration_s) and self.duration_s > 0):
            found.append(f"duration must be a positive number of seconds, got {self.duration_s}")
        elif self.fps >= 1:
            found += self.frame_count_problems()
        if self.fps < 1:
            found.append(f"fps must be a positive whole number, got {self.fps}")
        if self.viewport[0] < 1 or self.viewport[1] < 1:
            found.append(f"viewport must be at least 1x1 px, got {self.viewport[0]}x{self.viewport[1]}")
        for name, value, allowed in (
            ("red rule", self.red_rule, RED_RULE_CHOICES),
            ("attribute", self.attribute, ATTRIBUTE_CHOICES),
            ("strategy", self.strategy, STRATEGY_CHOICES),
        ):
            if value not in allowed:
                found.append(f"{name} must be one of {', '.join(allowed)}; got {value!r}")
        return found

    def frame_count_problems(self) -> list[str]:
        """Problems with the number of frames capture would record, round(duration x fps).

        Called only once the duration is a positive finite number and fps is at least 1.  A duration so
        long that duration x fps is not a finite number, or so short that it rounds to no frame at all,
        is a bad option value (exit 2), not a capture problem to report as INCONCLUSIVE.
        """
        try:
            frames = float(self.duration_s) * self.fps
        except OverflowError:  # an fps too large to turn into a float
            frames = math.inf
        if not math.isfinite(frames):
            return [f"duration {self.duration_s:g} s at {self.fps} fps is too many frames to capture"]
        if round(frames) < MIN_CAPTURE_FRAMES:
            return [f"duration {self.duration_s:g} s at {self.fps} fps is less than one frame; "
                    f"capture records round(duration x fps) frames"]
        return []

    def to_dict(self) -> dict[str, object]:
        """JSON-safe form, used in meta.json and in the job id hash."""
        return {
            "duration": float(self.duration_s),
            "viewport": {"width": int(self.viewport[0]), "height": int(self.viewport[1])},
            "fps": int(self.fps),
            "redRule": self.red_rule,
            "attribute": self.attribute,
            "strategy": self.strategy,
            "script": self.script,
        }


def settings_from_dict(data: dict[str, object]) -> AuditSettings:
    """Reverse of ``AuditSettings.to_dict`` (for reading meta.json back)."""
    viewport = data["viewport"]
    assert isinstance(viewport, dict)
    return AuditSettings(
        duration_s=float(data["duration"]),  # type: ignore[arg-type]
        viewport=(int(viewport["width"]), int(viewport["height"])),
        fps=int(data["fps"]),  # type: ignore[call-overload]
        red_rule=str(data["redRule"]),
        attribute=str(data["attribute"]),
        strategy=str(data["strategy"]),
        script=None if data.get("script") is None else str(data["script"]),
    )


def parse_viewport(text: str) -> tuple[int, int]:
    """'1024x768' -> (1024, 768).  Raises ValueError with a readable message."""
    match = re.fullmatch(r"\s*(\d+)\s*[xX]\s*(\d+)\s*", text)
    if match is None:
        raise ValueError(f"viewport must look like WIDTHxHEIGHT, for example 1024x768; got {text!r}")
    width, height = int(match.group(1)), int(match.group(2))
    if width < 1 or height < 1:
        raise ValueError(f"viewport must be at least 1x1 px, got {text!r}")
    return width, height


def default_base() -> Path:
    """``<current directory>/.flashtrace``."""
    return Path.cwd() / DEFAULT_BASE_NAME


def jobs_root(base: str | Path | None = None) -> Path:
    """The ``jobs`` directory under ``base`` (not created here)."""
    return (Path(base) if base is not None else default_base()) / JOBS_DIR_NAME


def settings_digest(url: str, settings: AuditSettings) -> str:
    """sha256 of the url followed by the settings as canonical JSON, as hex."""
    canonical = json.dumps(settings.to_dict(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256((url + canonical).encode("utf-8")).hexdigest()


def job_id_for(url: str, settings: AuditSettings, now: datetime | None = None) -> str:
    """'YYYYMMDD-HHMMSS-<first 6 hex of the digest>' with the time in UTC."""
    moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return f"{moment:%Y%m%d-%H%M%S}-{settings_digest(url, settings)[:6]}"


def new_job_dir(
    base: str | Path | None, url: str, settings: AuditSettings, now: datetime | None = None
) -> Path:
    """Create ``<base>/jobs/<job_id>/`` with empty frames/ and evidence/ folders; return its path."""
    root = jobs_root(base)
    root.mkdir(parents=True, exist_ok=True)
    stem = job_id_for(url, settings, now)
    for attempt in range(MAX_SAME_SECOND_JOBS):
        job_id = stem if attempt == 0 else f"{stem}-{attempt}"
        path = root / job_id
        try:
            # mkdir without exist_ok is atomic: if two processes race, exactly one wins.
            path.mkdir()
        except FileExistsError:
            continue
        (path / FRAMES_DIR_NAME).mkdir()
        (path / EVIDENCE_DIR_NAME).mkdir()
        return path
    raise RuntimeError(f"more than {MAX_SAME_SECOND_JOBS} identical jobs started in one second under {root}")


def job_dir_for(base: str | Path | None, job_id: str) -> Path:
    """Path of an existing job.  ValueError for a malformed id, FileNotFoundError if it is missing."""
    if not JOB_ID_PATTERN.match(job_id):
        raise ValueError(f"not a FlashTrace job id: {job_id!r}")
    path = jobs_root(base) / job_id
    if not path.is_dir():
        raise FileNotFoundError(f"no job {job_id!r} under {jobs_root(base)}")
    return path


def list_jobs(base: str | Path | None = None) -> list[str]:
    """Ids of the jobs under ``base``, oldest first (ids sort by their start time)."""
    root = jobs_root(base)
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir() if p.is_dir() and JOB_ID_PATTERN.match(p.name))


def read_meta(job_dir: str | Path) -> dict[str, object]:
    """The job's meta.json as a dict."""
    return json.loads((Path(job_dir) / META_JSON_NAME).read_text(encoding="utf-8"))


def write_meta(job_dir: str | Path, meta: dict[str, object]) -> None:
    """Write meta.json atomically (a reader never sees a half-written file)."""
    target = Path(job_dir) / META_JSON_NAME
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(meta, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, target)


def update_meta(job_dir: str | Path, **fields: object) -> dict[str, object]:
    """Merge ``fields`` into meta.json and return the new contents."""
    meta = read_meta(job_dir)
    meta.update(fields)
    write_meta(job_dir, meta)
    return meta
