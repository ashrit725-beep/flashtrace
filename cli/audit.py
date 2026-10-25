"""The audit pipeline as one function: page in, validated report out (SPEC 6.4, 8).

``run_audit`` is called by ``flashtrace audit``, by the benchmark runner (once per page, in worker
processes) and by the local server, so all three produce reports the same way:

    check settings -> resolve the page -> new job directory (meta.json keeps the settings)
    -> capture frames into <job>/frames -> engine analysis (frames streamed from disk one at a time)
    -> optional attribution (capture.attribute) -> fix proposals (capture.fix)
    -> build + validate the report -> write report.json and report.html (+ --out / --html copies)

Two more entry points work on a finished job directory, without re-capturing the baseline:
``reattribute`` (run attribution again, for example with ablation) and ``verify_fix_in_job``
(re-capture with one fix injected and record the result in the report).

Progress: ``progress(done, total)`` receives capture frame counts.  When that callable also has a
``phase(label, done=None, total=None)`` method it is told about the later phases too
("attributing 3/6", "proposing fixes"); the command line prints those lines on stderr.
"""

from __future__ import annotations

import dataclasses
import json
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from cli.html_report import render_html
from cli.jobs import (
    FRAMES_DIR_NAME,
    META_JSON_NAME,
    REPORT_HTML_NAME,
    REPORT_JSON_NAME,
    AuditSettings,
    jobs_root,
    list_jobs,
    new_job_dir,
    read_meta,
    settings_from_dict,
    update_meta,
    write_meta,
)
from cli.report import (
    build_report,
    capture_block,
    export_report,
    utc_now_iso,
    validate_report,
    with_attribution,
    write_report,
)

CaptureProgress = Callable[[int, int], None]
Attributor = Callable[..., list[dict[str, Any]]]

ATTRIBUTION_MODES: tuple[str, ...] = ("bbox", "ablate")


class AuditInputError(ValueError):
    """The audit cannot start as requested (bad option, missing file, unsupported URL)."""


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
    """capture.attribute.attribute for 'bbox' and 'ablate'; None for 'none' (no attribution)."""
    if mode == "none":
        return None
    from capture.attribute import attribute  # imported here: the capture package loads Playwright

    return attribute


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


def _saved_settings(settings: AuditSettings) -> dict[str, object]:
    """The settings as meta.json keeps them: the script path made absolute, so a later step that runs
    from another directory (fix verification, the server) still finds it."""
    saved = settings.to_dict()
    if settings.script is not None:
        saved["script"] = str(Path(settings.script).resolve())
    return saved


def _start_job(url: str, settings: AuditSettings, work_dir: str | Path | None, script: str | None) -> Path:
    """New job directory with meta.json (url, settings, jobDir) and a copy of the interaction script."""
    job_dir = new_job_dir(work_dir, url, settings)
    if script is not None:
        from capture.fix import JOB_SCRIPT_NAME

        # Verification re-runs exactly this script, even if the file is edited after the audit.
        (job_dir / JOB_SCRIPT_NAME).write_text(script, encoding="utf-8")
    write_meta(job_dir, {"jobId": job_dir.name, "jobDir": str(job_dir.resolve()), "url": url,
                         "settings": _saved_settings(settings), "status": "running",
                         "createdAt": utc_now_iso(), "verdict": None})
    return job_dir


def _phase(progress: Any | None, label: str, done: int | None = None, total: int | None = None) -> None:
    """Tell the progress callback about a later phase, when it has a ``phase`` method."""
    hook = getattr(progress, "phase", None)
    if callable(hook):
        hook(label, done, total)


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

    Raises AuditInputError before any work starts; other exceptions are real errors (the job's
    meta.json is marked "error" first).  ``measurements``, when given, receives
    capture_s and analysis_s (wall time, time.perf_counter) and the engine's general/red max rates.
    With ``settings.attribute`` "bbox" or "ablate" the report also carries culprits and fixes.
    """
    check_settings(settings)
    url = resolve_target(target)
    script = load_script(settings.script)
    attributor = find_attributor(settings.attribute)
    job_dir = _start_job(url, settings, work_dir, script)
    try:
        report = _audit_job(url, settings, script, attributor, job_dir, progress, measurements, browser)
        _write_outputs(report, job_dir, out, html_out)
    except BaseException as exc:
        update_meta(job_dir, status="error", error=_first_line(exc))
        raise
    code = exit_code_for(report["verdict"], no_fail)
    copies = [str(Path(out).resolve())] if out is not None else []
    update_meta(job_dir, status="done", verdict=report["verdict"], reason=report["reason"], finishedAt=utc_now_iso(),
                reportCopies=copies)
    return report, job_dir, code


def _audit_job(url: str, settings: AuditSettings, script: str | None, attributor: Attributor | None,
               job_dir: Path, progress: CaptureProgress | None, measurements: dict[str, float] | None,
               browser: Any | None) -> dict[str, Any]:
    """Capture, analyse, attribute, propose fixes and assemble; times capture and analysis separately."""
    captured_at = utc_now_iso()
    started = time.perf_counter()
    captured, capture_error = _capture_step(url, settings, script, job_dir, progress, browser)
    capture_s = time.perf_counter() - started
    started = time.perf_counter()
    analysis = _analysis_step(captured, settings)
    analysis_s = time.perf_counter() - started
    meta = captured.meta if captured else None
    culprits, fixes = _attribution_step(url, analysis, meta, settings, attributor, job_dir, progress, browser)
    report = build_report(url, settings, meta, analysis, job_dir, culprits=culprits, fixes=fixes,
                          capture_error=capture_error, captured_at=captured_at)
    validate_report(report)
    if measurements is not None:
        measurements.update({
            "capture_s": capture_s,
            "analysis_s": analysis_s,
            "general_max_rate": float(analysis.general_max_rate) if analysis else 0.0,
            "red_max_rate": float(analysis.red_max_rate) if analysis else 0.0,
        })
    return report


# --------------------------------------------------------------------------------------- attribution + fixes
def _call_attributor(attributor: Attributor, url: str, analysis: Any, capture_meta: Any, settings: AuditSettings,
                     job_dir: Path, progress: Any | None, browser: Any | None = None) -> list[dict[str, Any]]:
    """capture.attribute.attribute(...) with the shared contract's arguments, plus ``browser`` and ``progress``.

    When the caller handed run_audit its own Chromium (``browser``), the attributor gets it too:
    Playwright's sync API cannot start a second Playwright in a thread that already runs one, so
    attribution would otherwise fail and return no culprits.  ``progress`` forwards the attributor's
    (done, total, detail) counts to the phase hook as "attributing 3/6"; the detail (which candidate
    was suppressed) is not printed, because attribution.json records it.
    """
    def forward(done: int, total: int, detail: str) -> None:
        _phase(progress, "attributing", done, total)

    return list(attributor(url=url, analysis=analysis, capture_meta=capture_meta, settings=settings,
                           job_dir=job_dir, mode=settings.attribute, browser=browser, progress=forward))


def _attribution_step(url: str, analysis: Any | None, capture_meta: Any | None, settings: AuditSettings,
                      attributor: Attributor | None, job_dir: Path, progress: Any | None,
                      browser: Any | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(culprits, fixes): attribution, then fix proposals.  Both empty when attribution is off or nothing failed.

    Before proposing, every animation of the culprits that get fixes is read from the page (the report
    keeps only an element's first one), so a fix changes only the animation that explains the flashing.
    """
    if attributor is None or analysis is None or capture_meta is None or not analysis.violations:
        return [], []
    from capture.fix import propose_fixes, read_element_animations  # here: the capture package loads Playwright

    _phase(progress, f"attributing ({settings.attribute})")
    culprits = _call_attributor(attributor, url, analysis, capture_meta, settings, job_dir, progress, browser)
    _phase(progress, "proposing fixes")
    animations = read_element_animations(url, analysis, culprits, settings, job_dir, browser=browser)
    fixes = propose_fixes(culprits, analysis, settings, job_dir, capture=capture_block(capture_meta),
                          animations=animations)
    return culprits, fixes


# --------------------------------------------------------------------------------------- finished jobs
def load_job(job_dir: str | Path) -> tuple[dict[str, Any], dict[str, Any], AuditSettings]:
    """(meta, report, settings) of a finished job; FileNotFoundError when it has no report yet."""
    folder = Path(job_dir)
    report_path = folder / REPORT_JSON_NAME
    if not (folder / META_JSON_NAME).is_file() or not report_path.is_file():
        raise FileNotFoundError(f"{folder} is not a finished FlashTrace job (meta.json and report.json are needed)")
    meta = dict(read_meta(folder))
    report = json.loads(report_path.read_text(encoding="utf-8"))
    settings = settings_from_dict(meta["settings"])  # type: ignore[arg-type]
    return meta, report, settings


def stored_capture_meta(report: Mapping[str, Any], settings: AuditSettings, url: str) -> Any:
    """A capture.CaptureMeta rebuilt from a report's ``capture`` block (for attribution of a finished job)."""
    from capture import CaptureMeta

    block = report["capture"]
    return CaptureMeta(
        strategy=str(block["strategy"]), deterministic=bool(block["deterministic"]),
        frame_hashes=list(block["frameHashes"]), hash=str(block["hash"]),
        uncontrollable=list(block["uncontrollable"]), fallback_reason=block["fallbackReason"],
        notes=list(block["notes"]), viewport=settings.viewport, fps=settings.fps,
        duration_s=settings.duration_s, frame_count=int(block["frameCount"]), url=url, wall_time_s=0.0,
    )


def analyse_stored_frames(job_dir: Path, settings: AuditSettings, expected: int) -> Any | None:
    """Run the engine again over the frames on disk (bit-identical to the first analysis); None if < 2."""
    from capture import FrameSequence
    from engine import analyze

    frames = FrameSequence(Path(job_dir) / FRAMES_DIR_NAME)
    if len(frames) != expected:
        raise ValueError(f"{job_dir} holds {len(frames)} frames but its report says {expected}")
    if len(frames) < 2:
        return None
    return analyze(frames, fps=settings.fps, viewport=settings.viewport, red_rule=settings.red_rule)


def _save_job_report(report: dict[str, Any], job_dir: Path) -> None:
    """Validate, then write report.json and report.html inside the job."""
    validate_report(report)
    _write_outputs(report, job_dir, None, None)


def reattribute(job_dir: Path, mode: str = "ablate", progress: Any | None = None,
                browser: Any | None = None) -> dict[str, Any]:
    """Re-run attribution (and fix proposals) on a finished job; returns the written, validated report.

    Uses the frames and report already on disk: the frames are analysed again to get the engine's
    result (the baseline is not re-captured).  Ablation itself re-captures the page with candidates
    suppressed, as it would during a normal audit.  meta.json's attribute setting becomes ``mode``.
    ``browser`` (optional) is a Chromium the caller already runs in this thread, as for run_audit.
    """
    if mode not in ATTRIBUTION_MODES:
        raise AuditInputError(f"attribution mode must be one of {', '.join(ATTRIBUTION_MODES)}; got {mode!r}")
    job_dir = Path(job_dir)
    meta, report, stored = load_job(job_dir)
    settings = dataclasses.replace(stored, attribute=mode)
    attributor = find_attributor(mode)
    url = str(meta["url"])
    analysis = analyse_stored_frames(job_dir, settings, int(report["capture"]["frameCount"]))
    capture_meta = stored_capture_meta(report, settings, url) if analysis is not None else None
    culprits, fixes = _attribution_step(url, analysis, capture_meta, settings, attributor, job_dir, progress, browser)
    updated = with_attribution(report, culprits, fixes, mode, job_dir)
    _save_job_report(updated, job_dir)
    update_meta(job_dir, settings=_saved_settings(settings))
    return updated


def verify_fix_in_job(job_dir: Path, n: int, progress: Any | None = None, browser: Any | None = None) -> dict[str, Any]:
    """Verify ``fixes[n]`` (from 0) of a finished job by re-capturing with it; writes and returns the report.

    ``browser`` (optional) is a Chromium the caller already runs in this thread, as for run_audit.
    """
    from capture.fix import verify_fix

    job_dir = Path(job_dir)
    meta, report, settings = load_job(job_dir)
    _phase(progress, "verifying")
    updated = verify_fix(report, n, job_dir, str(meta["url"]), settings, progress=progress, browser=browser)
    _save_job_report(updated, job_dir)
    return updated


def _report_matches(candidate: Mapping[str, Any], report: Mapping[str, Any]) -> bool:
    """Same audit: same page, same capture time and the same frames (sequence hash)."""
    keys = ("url", "capturedAt")
    return (all(candidate.get(k) == report.get(k) for k in keys)
            and candidate.get("capture", {}).get("hash") == report.get("capture", {}).get("hash"))


def locate_job(report_path: Path, work_dir: str | Path | None = None) -> Path:
    """The job directory a report belongs to.

    1. report.json inside a job directory (next to meta.json): that directory.
    2. A copy written with ``audit --out``: the job whose meta.json lists it under "reportCopies".
    3. Any other copy: the job under ``work_dir`` whose report has the same url, capture time and frames.
    """
    path = Path(report_path).resolve()
    if path.name == REPORT_JSON_NAME and (path.parent / META_JSON_NAME).is_file():
        return path.parent
    report = json.loads(path.read_text(encoding="utf-8"))
    for job_id in reversed(list_jobs(work_dir)):  # newest first
        job = jobs_root(work_dir) / job_id
        meta = read_meta(job)
        if str(path) in (meta.get("reportCopies") or []):
            return job
        own = job / REPORT_JSON_NAME
        if own.is_file() and _report_matches(json.loads(own.read_text(encoding="utf-8")), report):
            return job
    raise FileNotFoundError(
        f"no job found for {report_path}: pass the report.json inside the job folder, or use --work-dir to name "
        "the folder the audit used"
    )
