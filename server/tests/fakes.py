"""A fast stand-in for the audit pipeline, so the API tests run without a browser.

``FakePipeline`` replaces ``cli.audit.run_audit``, ``cli.audit.reattribute`` and
``cli.audit.verify_fix_in_job``.  It writes what the real functions write - a cli/jobs.py job directory
with PNG frames, a schema-valid report.json (FAIL, one culprit, one fix), report.html and meta.json - and
it calls the progress callback the way the capture does, so cancelling behaves the same way.

Each step can be held: while ``hold_audit`` (capture), ``hold_attribution`` or ``hold_follow_up`` is
cleared, that step keeps waiting - and keeps calling the progress callback, like a real capture between
frames, so cancelling still works - until the event is set again.  When the progress object has the
``phase(label, done, total)`` hook of cli.audit, the fakes report their later steps through it.

The module ends with small helpers the tests share (submit a job, poll until a state is reached).
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from PIL import Image

from cli.jobs import FRAMES_DIR_NAME, REPORT_HTML_NAME, REPORT_JSON_NAME, AuditSettings, new_job_dir, update_meta, write_meta
from cli.report import DISCLAIMER, build_report, utc_now_iso, validate_report, write_report

FRAME_COUNT = 4
HOLD_POLL_S = 0.01
WAIT_TIMEOUT_S = 30.0
POLL_S = 0.02
FAKE_REASON = "Fake audit: 4 qualifying general flashes in one second (test fixture)."


class StubCaptureMeta:
    """What build_report needs from capture.CaptureMeta, for FRAME_COUNT deterministic frames."""

    def __init__(self, frame_count: int) -> None:
        self.frame_count = frame_count

    def to_dict(self) -> dict[str, object]:
        hashes = [hashlib.sha256(bytes([index])).hexdigest() for index in range(self.frame_count)]
        return {"strategy": "waapi_step", "deterministic": True, "frameCount": self.frame_count,
                "hash": hashlib.sha256("".join(hashes).encode()).hexdigest(), "frameHashes": hashes,
                "fallbackReason": None, "uncontrollable": [], "notes": []}


def measurement(verdict: str) -> dict[str, object]:
    """A report.schema.json measurement object."""
    failing = verdict == "FAIL"
    return {"verdict": verdict, "generalMaxRatePerSec": 4.0 if failing else 0.0, "redMaxRatePerSec": 0.0,
            "qualifyingGeneral": 8 if failing else 0, "qualifyingRed": 0, "deterministic": True}


CULPRIT: dict[str, Any] = {
    "rank": 1, "selector": "#box", "nodeType": "DIV", "ftId": "ft-1",
    "rect": {"x": 100, "y": 100, "w": 400, "h": 300},
    "animation": {"name": "ft-blink", "duration": 250, "keyframes": [{"offset": 0}, {"offset": 0.5}]},
    "overlap": 1.0, "ablationDelta": None, "inMinimalSet": False,
}
FIX: dict[str, Any] = {
    "culprit": 1, "kind": "rate", "css": '[data-ft-id="ft-1"]{animation-duration:1000ms!important}',
    "rationale": "Slows the blink to 1 flash per second (test fixture).", "before": measurement("FAIL"),
    "after": None, "verified": False,
}


def fake_report(url: str, settings: AuditSettings, job_dir: Path) -> dict[str, Any]:
    """A schema-valid FAIL report with one culprit and one fix."""
    report = build_report(url, settings, StubCaptureMeta(FRAME_COUNT), None, job_dir,
                          culprits=[dict(CULPRIT)], fixes=[dict(FIX)])
    report["verdict"], report["reason"] = "FAIL", FAKE_REASON
    validate_report(report)
    return report


def write_frames(job_dir: Path, count: int) -> None:
    for index in range(count):
        shade = 255 if index % 2 else 0
        Image.new("RGB", (8, 8), (shade, shade, shade)).save(job_dir / FRAMES_DIR_NAME / f"{index:06d}.png")


def load_report(job_dir: Path) -> dict[str, Any]:
    return json.loads((job_dir / REPORT_JSON_NAME).read_text(encoding="utf-8"))


def save_report(job_dir: Path, report: dict[str, Any]) -> dict[str, Any]:
    validate_report(report)
    write_report(report, job_dir / REPORT_JSON_NAME)
    return report


class FakePipeline:
    """Replacement for the three cli.audit functions the runner calls (see the module docstring)."""

    def __init__(self) -> None:
        self.audited: list[str] = []
        self.ablated: list[Path] = []
        self.verified: list[tuple[Path, int]] = []
        self.hold_audit = threading.Event()
        self.hold_attribution = threading.Event()
        self.hold_follow_up = threading.Event()
        self.audit_waiting = threading.Event()  # set while an audit sits in the held capture loop
        self.release_all()
        self.fail_with: Exception | None = None

    def release_all(self) -> None:
        for event in (self.hold_audit, self.hold_attribution, self.hold_follow_up):
            event.set()

    @staticmethod
    def _hold(event: threading.Event, tick: Callable[[], None], waiting: threading.Event | None = None) -> None:
        """Wait while ``event`` is cleared, calling ``tick`` (a progress call) every few milliseconds."""
        while not event.is_set():
            if waiting is not None:
                waiting.set()
            tick()  # raises JobCancelled once the job is cancelled
            time.sleep(HOLD_POLL_S)
        if waiting is not None:
            waiting.clear()

    def run_audit(self, target: str, settings: AuditSettings, work_dir: Path | None = None,
                  progress: Callable[[int, int], None] | None = None, **_ignored: Any) -> tuple[dict[str, Any], Path, int]:
        self.audited.append(target)
        job_dir = new_job_dir(work_dir, target, settings)
        write_meta(job_dir, {"jobId": job_dir.name, "jobDir": str(job_dir.resolve()), "url": target,
                             "settings": settings.to_dict(), "status": "running", "createdAt": utc_now_iso(),
                             "verdict": None})
        try:
            self._capture(job_dir, progress or (lambda done, total: None))
            self._attribute(settings, progress)
            if self.fail_with is not None:
                raise self.fail_with
            report = save_report(job_dir, fake_report(target, settings, job_dir))
            (job_dir / REPORT_HTML_NAME).write_text(f"<!doctype html><title>Report</title><p>{DISCLAIMER}</p>", encoding="utf-8")
        except BaseException as exc:
            update_meta(job_dir, status="error", error=str(exc))
            raise
        update_meta(job_dir, status="done", verdict=report["verdict"], reason=report["reason"])
        return report, job_dir, 1

    def _capture(self, job_dir: Path, progress: Callable[[int, int], None]) -> None:
        """Write the frames, reporting progress; wait (still reporting) while hold_audit is cleared."""
        write_frames(job_dir, FRAME_COUNT)
        for done in range(1, FRAME_COUNT):
            progress(done, FRAME_COUNT)
        self._hold(self.hold_audit, lambda: progress(FRAME_COUNT - 1, FRAME_COUNT), self.audit_waiting)
        progress(FRAME_COUNT, FRAME_COUNT)

    def _attribute(self, settings: AuditSettings, progress: Any) -> None:
        """Report attribution steps through the phase hook, as cli.audit does."""
        phase = getattr(progress, "phase", None)
        if settings.attribute == "none" or phase is None:
            return
        self._hold(self.hold_attribution, lambda: phase(f"attributing ({settings.attribute})"))
        if settings.attribute == "ablate":
            phase("ablation 1: suppressed ['#box']", 1, 2)
        phase("proposing fixes")

    def reattribute(self, job_dir: Path, mode: str = "ablate", progress: Any = None) -> dict[str, Any]:
        tick = (lambda: progress.phase("ablation 1: suppressed ['#box']", 1, 2)) if progress else (lambda: None)
        self._hold(self.hold_follow_up, tick)
        self.ablated.append(job_dir)
        report = load_report(job_dir)
        report["settings"]["attribute"] = mode
        for culprit in report["culprits"]:
            culprit["ablationDelta"], culprit["inMinimalSet"] = 8.0, True
        return save_report(job_dir, report)

    def verify_fix_in_job(self, job_dir: Path, n: int, progress: Any = None) -> dict[str, Any]:
        tick = (lambda: progress(1, FRAME_COUNT)) if progress else (lambda: None)
        self._hold(self.hold_follow_up, tick)
        self.verified.append((job_dir, n))
        report = load_report(job_dir)
        report["fixes"][n]["after"], report["fixes"][n]["verified"] = measurement("PASS"), True
        return save_report(job_dir, report)


# --------------------------------------------------------------------------------------- helpers
def submit(client: TestClient, url: str = "https://example.test/page", **fields: Any) -> str:
    """POST /jobs and return the new id (asserting the 201)."""
    response = client.post("/jobs", json={"url": url, **fields})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def wait_for(client: TestClient, job_id: str, check: Callable[[dict[str, Any]], bool],
             timeout: float = WAIT_TIMEOUT_S) -> dict[str, Any]:
    """Poll GET /jobs/{id} until ``check(summary)`` holds; fail with the last summary after ``timeout``."""
    deadline = time.monotonic() + timeout
    summary: dict[str, Any] = {}
    while time.monotonic() < deadline:
        summary = client.get(f"/jobs/{job_id}").json()
        if check(summary):
            return summary
        time.sleep(POLL_S)
    raise AssertionError(f"job {job_id} did not reach the expected state; last summary: {summary}")


def is_finished(summary: dict[str, Any]) -> bool:
    """Nothing more will change (the web UI's isFinished): error, cancelled, or done with phase done."""
    status = summary.get("status")
    return status in ("error", "cancelled") or (status == "done" and summary.get("phase") == "done")


def wait_until_finished(client: TestClient, job_id: str) -> dict[str, Any]:
    return wait_for(client, job_id, is_finished)


def wait_until_gone(path: Path, timeout: float = WAIT_TIMEOUT_S) -> None:
    deadline = time.monotonic() + timeout
    while path.exists() and time.monotonic() < deadline:
        time.sleep(POLL_S)
    assert not path.exists(), f"{path} still exists"
