"""The audit pipeline as one function: page in, validated report out (SPEC 6.4, 8).

``run_audit`` is called by ``flashtrace audit``, by the benchmark runner (once per page, in worker
processes) and later by the local server, so all three produce reports the same way:

    check settings -> resolve the page -> new job directory -> capture frames into <job>/frames
    -> engine analysis (frames streamed from disk one at a time) -> optional attribution
    -> build + validate the report -> write report.json and report.html (+ --out / --html copies)
"""

from __future__ import annotations

import importlib
import importlib.util
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from cli.html_report import render_html
from cli.jobs import (
    FRAMES_DIR_NAME,
    REPORT_HTML_NAME,
    REPORT_JSON_NAME,
    AuditSettings,
    new_job_dir,
    update_meta,
    write_meta,
)
from cli.report import build_report, export_report, utc_now_iso, validate_report, write_report

CaptureProgress = Callable[[int, int], None]
Attributor = Callable[..., list[dict[str, Any]]]

ATTRIBUTION_UNAVAILABLE = "attribution is not available yet (milestone M4/M5)"


class AuditInputError(ValueError):
    """The audit cannot start as requested (bad option, missing file, unsupported URL)."""


class AttributionUnavailable(RuntimeError):
    """--attribute bbox|ablate was asked for but capture.attribute does not provide attribute() yet."""


# --------------------------------------------------------------------------------------- inputs
def check_settings(settings: AuditSettings) -> None:
    """Raise AuditInputError listing every problem with ``settings``."""
    problems = settings.problems()
    if problems:
        raise AuditInputError("; ".join(problems))


def resolve_target(target: str) -> str:
    """http(s) URL, file:// URL or existing local path -> the URL the browser will load."""
    from capture import resolve_url  # imported here so that `flashtrace --help` does not load Playwright

    parsed = urlparse(target)
    if parsed.scheme == "file" and not Path(unquote(parsed.path)).exists():
        raise AuditInputError(f"no such file: {unquote(parsed.path)!r}")
    try:
        return resolve_url(target)
    except (FileNotFoundError, ValueError) as exc:
        raise AuditInputError(str(exc)) from None


def load_script(path: str | None) -> str | None:
    """The interaction script's JavaScript source, or None when no script was given."""
    if path is None:
        return None
    file = Path(path)
    if not file.is_file():
        raise AuditInputError(f"no such interaction script: {path!r}")
    return file.read_text(encoding="utf-8")


def find_attributor(mode: str) -> Attributor | None:
    """capture.attribute.attribute for bbox/ablate; None for 'none'; AttributionUnavailable if missing."""
    if mode == "none":
        return None
    if importlib.util.find_spec("capture.attribute") is None:
        raise AttributionUnavailable(ATTRIBUTION_UNAVAILABLE)
    function = getattr(importlib.import_module("capture.attribute"), "attribute", None)
    if not callable(function):
        raise AttributionUnavailable(ATTRIBUTION_UNAVAILABLE)
    return function


def exit_code_for(verdict: str, no_fail: bool = False) -> int:
    """SPEC 6.4: 0 PASS, 1 FAIL, 1 INCONCLUSIVE (0 with --no-fail).  Errors (2) are the caller's job."""
    if verdict == "PASS" or (verdict == "INCONCLUSIVE" and no_fail):
        return 0
    return 1


# --------------------------------------------------------------------------------------- steps
def _first_line(exc: BaseException) -> str:
    text = str(exc).strip()
    return f"{type(exc).__name__}: {text.splitlines()[0]}" if text else type(exc).__name__


def _capture_step(url: str, settings: AuditSettings, script: str | None, job_dir: Path,
                  progress: CaptureProgress | None, browser: Any | None) -> tuple[Any | None, str | None]:
    """Run capture() into <job>/frames.  Returns (CaptureResult, None) or (None, error text).

    A capture that fails after the inputs were accepted is not a tool error: SPEC 6.4 makes it an
    INCONCLUSIVE report that says why.
    """
    from capture import capture

    try:
        result = capture(url, duration_s=settings.duration_s, viewport=settings.viewport, fps=settings.fps,
                         interaction_script=script, strategy=settings.strategy,
                         out_dir=job_dir / FRAMES_DIR_NAME, progress=progress, browser=browser)
    except Exception as exc:  # noqa: BLE001 - any capture failure becomes an INCONCLUSIVE report
        return None, _first_line(exc)
    return result, None


def _analysis_step(captured: Any | None, settings: AuditSettings) -> Any | None:
    """engine.analyze over the frames (streamed from disk); None when there are fewer than 2 frames."""
    from engine import analyze

    if captured is None or len(captured.frames) < 2:
        return None
    return analyze(captured.frames, fps=settings.fps, viewport=settings.viewport, red_rule=settings.red_rule)


def _write_outputs(report: dict[str, Any], job_dir: Path, out: str | Path | None, html_out: str | Path | None) -> None:
    """report.json + report.html in the job, plus the --out and --html copies when asked for."""
    write_report(report, job_dir / REPORT_JSON_NAME)
    page = render_html(report, embed_frames=True, base_dir=job_dir)
    (job_dir / REPORT_HTML_NAME).write_text(page, encoding="utf-8")
    if out is not None:
        export_report(report, job_dir, out)
    if html_out is not None:
        target = Path(html_out)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(page, encoding="utf-8")


def _start_job(url: str, settings: AuditSettings, work_dir: str | Path | None) -> Path:
    job_dir = new_job_dir(work_dir, url, settings)
    write_meta(job_dir, {"jobId": job_dir.name, "url": url, "settings": settings.to_dict(),
                         "status": "running", "createdAt": utc_now_iso(), "verdict": None})
    return job_dir


# --------------------------------------------------------------------------------------- pipeline
def run_audit(
    target: str,
    settings: AuditSettings,
    work_dir: str | Path | None = None,
    out: str | Path | None = None,
    html_out: str | Path | None = None,
    no_fail: bool = False,
    progress: CaptureProgress | None = None,
    measurements: dict[str, float] | None = None,
    browser: Any | None = None,
) -> tuple[dict[str, Any], Path, int]:
    """Audit one page.  Returns (report, job directory, exit code).

    Raises AuditInputError or AttributionUnavailable before any work starts; other exceptions are
    real errors (the job's meta.json is marked "error" first).  ``measurements``, when given, receives
    capture_s and analysis_s (wall time, time.perf_counter) and the engine's general/red max rates.
    """
    check_settings(settings)
    url = resolve_target(target)
    script = load_script(settings.script)
    attributor = find_attributor(settings.attribute)
    job_dir = _start_job(url, settings, work_dir)
    try:
        report = _audit_job(url, settings, script, attributor, job_dir, progress, measurements, browser)
        _write_outputs(report, job_dir, out, html_out)
    except BaseException as exc:
        update_meta(job_dir, status="error", error=_first_line(exc))
        raise
    code = exit_code_for(report["verdict"], no_fail)
    update_meta(job_dir, status="done", verdict=report["verdict"], reason=report["reason"], finishedAt=utc_now_iso())
    return report, job_dir, code


def _audit_job(url: str, settings: AuditSettings, script: str | None, attributor: Attributor | None,
               job_dir: Path, progress: CaptureProgress | None, measurements: dict[str, float] | None,
               browser: Any | None) -> dict[str, Any]:
    """Capture, analyse, attribute and assemble; times capture and analysis separately."""
    captured_at = utc_now_iso()
    started = time.perf_counter()
    captured, capture_error = _capture_step(url, settings, script, job_dir, progress, browser)
    capture_s = time.perf_counter() - started
    started = time.perf_counter()
    analysis = _analysis_step(captured, settings)
    analysis_s = time.perf_counter() - started
    culprits: list[dict[str, Any]] = []
    if attributor is not None and analysis is not None and analysis.violations:
        culprits = attributor(url=url, analysis=analysis, capture_meta=captured.meta, settings=settings,
                              job_dir=job_dir, mode=settings.attribute)
    report = build_report(url, settings, captured.meta if captured else None, analysis, job_dir,
                          culprits=culprits, capture_error=capture_error, captured_at=captured_at)
    validate_report(report)
    if measurements is not None:
        measurements.update({
            "capture_s": capture_s,
            "analysis_s": analysis_s,
            "general_max_rate": float(analysis.general_max_rate) if analysis else 0.0,
            "red_max_rate": float(analysis.red_max_rate) if analysis else 0.0,
        })
    return report
