"""The server's view of jobs: an index of job directories, and the JobSummary JSON of docs/API.md.

Every job is an ordinary job directory from cli/jobs.py (``<work>/jobs/<id>/`` holding frames/,
evidence/, report.json, report.html and meta.json), so ``flashtrace audit`` and the server share one
history.  When the server starts it reads every meta.json once to rebuild its index.  After that the
in-memory index is the source of truth; it is guarded by a lock because the API threads and the runner
thread both use it, and every status change is written back to meta.json.

A queued job's directory exists from the moment it is submitted, so its id is known at once.  But
``run_audit`` always creates a job directory of its own, so the audit is pointed at ``<job>/run/`` as its
work directory and builds ``<job>/run/jobs/<audit id>/`` there.  When it finishes, ``adopt_run`` moves
those files up into ``<job>/`` and the job looks exactly like one made by ``flashtrace audit``.

Jobs that were queued or running when the server stopped become "error" on the next start
("The server stopped during the job.").  Two exceptions:

* a follow-up task (ablation or verification) never takes away the audit it works on.  Before it starts,
  the report files are copied to ``<job>/before-follow-up/``; if it fails or the server stops, they are
  put back and the job stays "done", with the reason in ``error`` (see ``mark_follow_up_failed``);
* a job that ``flashtrace audit`` is running in the same work directory is not the server's to recover.
  The server leaves its meta.json alone and re-reads it while it is still running (``managed`` False).

Deleting a job survives a server stop.  DELETE first leaves a ``deleted`` marker file in the folder.  A
running job's folder is removed only after its task stops, so if the server stops first, the next start
finds the marker and finishes the deletion (``remove_deleted_jobs``) instead of listing the job again.
A folder is removed by renaming it to ``<id>.deleted`` first, a name that is not a job id, so it leaves
the history in one step even if removing its files fails halfway.

A status that cannot be written to meta.json (a full disk, a read-only folder, a damaged file) is kept
in memory and logged; it never raises, so recording how a task ended cannot fail (server/runner.py).
"""

from __future__ import annotations

import dataclasses
import logging
import os
import shutil
import threading
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cli.jobs import (
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
from cli.report import ATTRIBUTION_NOTES_NAME, utc_now_iso

STATUSES: tuple[str, ...] = ("queued", "running", "done", "error", "cancelled")
PHASES: tuple[str, ...] = ("queued", "capturing", "analyzing", "attributing", "ablating", "verifying", "done", "error")
VERDICTS: tuple[str, ...] = ("PASS", "FAIL", "INCONCLUSIVE")
ACTIVE_STATUSES: tuple[str, ...] = ("queued", "running")

RUN_DIR_NAME = "run"  # <job>/run/ is where run_audit builds the job before adopt_run moves it up
SERVER_SOURCE = "server"
STOPPED_DURING = "The server stopped during the job."
STOPPED_BEFORE = "The server stopped before the job started."

# meta.json keys that belong to the server's job and survive adopt_run unchanged.
SERVER_META_KEYS: tuple[str, ...] = ("jobId", "createdAt", "source")

# meta.json key the server writes with every status change.  Together with "source" it tells, after a
# restart, whether a "running" job was the server's (then the server was stopped) or a CLI audit's.
STATUS_WRITER_KEY = "statusWrittenBy"

# The phase a job read from disk is shown in when its meta.json does not say (jobs from the CLI).
DEFAULT_PHASE = {"queued": "queued", "running": "capturing", "done": "done", "error": "error", "cancelled": "error"}

# The phases that make sense with each status.  A stored phase outside this list (for example "error"
# on a job whose status is "done") is replaced by DEFAULT_PHASE, so the UI never sees a contradiction.
PHASES_FOR_STATUS: dict[str, tuple[str, ...]] = {
    "queued": ("queued",),
    "running": ("capturing", "analyzing", "attributing", "ablating", "verifying"),
    "done": ("done",),
    "error": ("error",),
    "cancelled": PHASES,  # a cancelled job keeps the phase it was stopped in
}

# A follow-up task may rewrite these files; they are copied here first and put back if it does not finish.
FOLLOW_UP_PHASES: tuple[str, ...] = ("queued", "ablating", "verifying")  # a finished job queued again, or re-running
FOLLOW_UP_BACKUP_DIR_NAME = "before-follow-up"
REPORT_FILES: tuple[str, ...] = (REPORT_JSON_NAME, REPORT_HTML_NAME, ATTRIBUTION_NOTES_NAME)  # put back whole
FOLLOW_UP_FILES: tuple[str, ...] = (*REPORT_FILES, META_JSON_NAME)  # meta.json: only its settings are put back

DELETED_MARKER_NAME = "deleted"  # DELETE leaves this file in the job folder (see the module docstring)
DELETED_SUFFIX = ".deleted"  # a folder being removed is renamed <id>.deleted first; that is not a job id

log = logging.getLogger(__name__)


@dataclass
class Progress:
    """docs/API.md Progress: e.g. current 312, total 600, label "frames 312/600"."""

    current: int = 0
    total: int = 0
    label: str = ""

    def to_dict(self) -> dict[str, object]:
        return {"current": int(self.current), "total": int(self.total), "label": self.label}


@dataclass
class JobRecord:
    """Everything the server keeps in memory about one job (its directory holds the rest)."""

    id: str
    job_dir: Path
    url: str
    created_at: str
    settings: AuditSettings
    status: str = "queued"
    phase: str = "queued"
    progress: Progress = field(default_factory=Progress)
    verdict: str | None = None
    reason: str | None = None
    error: str | None = None
    frame_count: int | None = None  # report.capture.frameCount, cached for the frame endpoint
    cancel: threading.Event = field(default_factory=threading.Event, repr=False)
    delete_when_stopped: bool = False  # DELETE arrived while the runner was working on this job
    stopped_by_shutdown: bool = False  # the server is shutting down, not the user cancelling
    # Seconds without a progress callback after which the runner's watchdog stopped the job's hung
    # browser; None unless that happened (server/runner.py, Watchdog).
    timed_out_after: float | None = None
    last_callback: float = 0.0  # time.monotonic() of the task's latest progress callback (for the watchdog)
    # False for a job another process (``flashtrace audit``) created and the server never worked on:
    # the server does not write its status, and re-reads it from disk while it is still running.
    managed: bool = True

    @property
    def total_frames(self) -> int:
        """How many frames the audit records: round(duration x fps), as in capture."""
        return round(self.settings.duration_s * self.settings.fps)

    @property
    def active(self) -> bool:
        """Queued or running: the report may not be read and no other task may be added."""
        return self.status in ACTIVE_STATUSES


# --------------------------------------------------------------------------------------- summary
def queue_label(jobs_ahead: int) -> str:
    """Progress label of a queued job, e.g. 'waiting for 2 jobs ahead'."""
    if jobs_ahead <= 0:
        return "starting"
    return f"waiting for {jobs_ahead} job{'s' if jobs_ahead != 1 else ''} ahead"


def job_summary(record: JobRecord, jobs_ahead: int | None = None) -> dict[str, Any]:
    """The JobSummary object of docs/API.md for ``record``."""
    progress = record.progress.to_dict()
    if record.status == "queued" and jobs_ahead is not None:
        progress["label"] = queue_label(jobs_ahead)
    width, height = record.settings.viewport
    return {
        "id": record.id,
        "url": record.url,
        "createdAt": record.created_at,
        "status": record.status,
        "phase": record.phase,
        "progress": progress,
        # Exactly the three fields POST /jobs accepts, so the UI can resubmit them unchanged ("Retry").
        "settings": {
            "duration": float(record.settings.duration_s),
            "viewport": {"width": int(width), "height": int(height)},
            "attribute": record.settings.attribute,
        },
        "verdict": record.verdict,
        "reason": record.reason,
        "error": record.error,
    }


# --------------------------------------------------------------------------------------- disk
def save_state(record: JobRecord) -> None:
    """Write the record's status fields into its meta.json; never raises.

    A job deleted meanwhile is skipped.  Any other failure (a full disk, a read-only folder, a damaged
    meta.json) is logged and the status stays in memory, which is the source of truth while the server
    runs.  The STATUS_WRITER_KEY says the server set this status, so a restart knows the job is the server's.
    """
    try:
        update_meta(record.job_dir, status=record.status, phase=record.phase, verdict=record.verdict,
                    reason=record.reason, error=record.error, **{STATUS_WRITER_KEY: SERVER_SOURCE})
    except FileNotFoundError:
        pass
    except (OSError, ValueError) as exc:  # ValueError: meta.json is not valid JSON
        log.warning("job %s: could not write status %r to meta.json (%s); it is kept in memory",
                    record.id, record.status, exc)


def new_meta(record: JobRecord) -> dict[str, object]:
    """meta.json of a job the server has just accepted."""
    return {
        "jobId": record.id,
        "url": record.url,
        "settings": record.settings.to_dict(),
        "createdAt": record.created_at,
        "status": record.status,
        "phase": record.phase,
        "verdict": None,
        "reason": None,
        "error": None,
        "source": SERVER_SOURCE,
    }


def _known(value: object, allowed: tuple[str, ...], default: str | None) -> str | None:
    """``value`` when it is one of ``allowed``, else ``default`` (meta.json may be old or hand-edited)."""
    return value if isinstance(value, str) and value in allowed else default


def _text_or_none(value: object) -> str | None:
    return value if isinstance(value, str) else None


def phase_for(status: str, stored: object) -> str:
    """The stored phase when it fits ``status`` (PHASES_FOR_STATUS), else the status's default phase."""
    return stored if isinstance(stored, str) and stored in PHASES_FOR_STATUS[status] else DEFAULT_PHASE[status]


def managed_by_server(meta: Mapping[str, object]) -> bool:
    """True when the server created this job or has set its status (see STATUS_WRITER_KEY)."""
    return meta.get("source") == SERVER_SOURCE or meta.get(STATUS_WRITER_KEY) == SERVER_SOURCE


def record_from_disk(job_dir: Path) -> JobRecord | None:
    """Rebuild a JobRecord from a job directory's meta.json; None when it cannot be read.

    A directory without a readable meta.json (half-created, or edited by hand) is left out of the index.
    """
    try:
        meta = read_meta(job_dir)
        settings = settings_from_dict(meta["settings"])
    except (OSError, ValueError, KeyError, TypeError, AssertionError):
        return None
    status = _known(meta.get("status"), STATUSES, "error") or "error"
    record = JobRecord(
        id=job_dir.name,
        job_dir=job_dir,
        url=str(meta.get("url", "")),
        created_at=str(meta.get("createdAt", "")),
        settings=settings,
        status=status,
        phase=phase_for(status, meta.get("phase")),
        verdict=_known(meta.get("verdict"), VERDICTS, None),
        reason=_text_or_none(meta.get("reason")),
        error=_text_or_none(meta.get("error")),
        managed=managed_by_server(meta),
    )
    total = record.total_frames
    record.progress = Progress(total if status == "done" else 0, total, status)
    return record


def recover_if_interrupted(record: JobRecord) -> None:
    """A server job still queued or running at start-up was cut off by a server stop.

    Without a report it becomes "error".  With one, the cut-off task was a follow-up (ablation or
    verification): its report files are put back as they were and the job is "done" again, with the
    reason in ``error``.  A job the server does not manage (a CLI audit still running) is left alone.
    """
    if not record.active or not record.managed:
        return
    message = STOPPED_DURING if record.status == "running" else STOPPED_BEFORE
    # The half-built audit of the interrupted run is of no use; the job's own report (if any) is kept.
    shutil.rmtree(record.job_dir / RUN_DIR_NAME, ignore_errors=True)
    if interrupted_follow_up(record) and restore_follow_up_files(record.job_dir) \
            and (record.job_dir / REPORT_JSON_NAME).is_file():
        fresh = record_from_disk(record.job_dir)  # the restored meta.json may hold the old settings again
        if fresh is not None:
            record.settings = fresh.settings
        set_follow_up_failed(record, message)
    else:
        record.status, record.phase, record.error = "error", "error", message
        record.progress = Progress(0, record.total_frames, "stopped")
    save_state(record)


def interrupted_follow_up(record: JobRecord) -> bool:
    """True when the cut-off task was an ablation or verification of a finished audit.

    Such a job already has its report and is queued again, ablating or verifying.  A new audit only
    gets report.json in its very last step (adopt_run), so a new audit cut off at any point is not one
    (its phase is "capturing" ... "attributing", or "ablating" without a report yet).
    """
    return record.phase in FOLLOW_UP_PHASES and (record.job_dir / REPORT_JSON_NAME).is_file()


def set_follow_up_failed(record: JobRecord, message: str) -> None:
    """A follow-up did not finish, but the audit's report is intact: "done" again, the reason in ``error``."""
    record.status, record.phase, record.error = "done", "done", message
    record.progress = Progress(record.total_frames, record.total_frames, "done; the last re-run did not finish")


def load_records(work_dir: Path) -> list[JobRecord]:
    """Every readable job under ``<work_dir>/jobs``, with interrupted ones already marked "error".

    Deletions that a server stop cut off are finished first, so a deleted job never comes back.
    """
    remove_deleted_jobs(work_dir)
    records: list[JobRecord] = []
    for job_id in list_jobs(work_dir):
        record = record_from_disk(jobs_root(work_dir) / job_id)
        if record is not None:
            recover_if_interrupted(record)
            records.append(record)
    return records


def _move_replacing(source: Path, target: Path) -> None:
    """Move ``source`` to ``target``, first removing whatever is at ``target`` (file or folder)."""
    if target.is_dir() and not target.is_symlink():
        shutil.rmtree(target)
    elif target.exists() or target.is_symlink():
        target.unlink()
    os.replace(source, target)


def adopt_run(job_dir: Path, audit_dir: Path) -> None:
    """Move a finished audit's files from ``<job>/run/jobs/<audit id>/`` up into ``<job>/``.

    meta.json is merged, not moved: the audit's fields (resolved url, settings, verdict, ...) are kept,
    but the job keeps its own id and creation time, so links to it stay valid.
    """
    audit_meta = read_meta(audit_dir)
    own_meta = read_meta(job_dir)
    for entry in sorted(audit_dir.iterdir()):
        if entry.name != META_JSON_NAME:
            _move_replacing(entry, job_dir / entry.name)
    merged: dict[str, object] = {**audit_meta, "auditJobId": audit_meta.get("jobId")}
    merged.update({key: own_meta[key] for key in SERVER_META_KEYS if key in own_meta})
    if "jobDir" in merged:  # run_audit recorded where it built the job; the job now lives here
        merged["jobDir"] = str(job_dir.resolve())
    write_meta(job_dir, merged)
    shutil.rmtree(job_dir / RUN_DIR_NAME, ignore_errors=True)


def mark_deleted(job_dir: Path) -> None:
    """Leave the DELETED_MARKER_NAME file in the job folder, so its deletion survives a server stop."""
    try:
        (job_dir / DELETED_MARKER_NAME).touch()
    except FileNotFoundError:  # the folder is already gone
        pass
    except OSError as exc:
        log.warning("job %s: could not mark the folder as deleted (%s)", job_dir.name, exc)


def delete_job_files(job_dir: Path) -> None:
    """Remove a job directory and everything in it (a folder that is already gone is fine).

    The folder is renamed to ``<id>.deleted`` first: no listing shows it from then on, even if removing
    its files fails halfway, and ``remove_deleted_jobs`` clears such a leftover at the next start.
    """
    trash = job_dir.with_name(job_dir.name + DELETED_SUFFIX)
    try:
        os.replace(job_dir, trash)
    except FileNotFoundError:
        return
    except OSError:  # for example an older leftover with that name: remove the folder where it is
        trash = job_dir
    shutil.rmtree(trash, ignore_errors=True)


def remove_deleted_jobs(work_dir: Path) -> None:
    """Finish deletions a server stop cut off: folders with the marker, and ``<id>.deleted`` leftovers."""
    root = jobs_root(work_dir)
    if not root.is_dir():
        return
    for entry in sorted(root.iterdir()):
        if not entry.is_dir():
            continue
        if entry.name.endswith(DELETED_SUFFIX):
            shutil.rmtree(entry, ignore_errors=True)
        elif (entry / DELETED_MARKER_NAME).exists():
            delete_job_files(entry)


# --------------------------------------------------------------------------------------- follow-up backup
def backup_follow_up_files(job_dir: Path) -> None:
    """Copy the files a follow-up task may rewrite into ``<job>/before-follow-up/``.

    The copy is made in a temporary folder that is renamed at the end, so the backup folder is either
    complete or absent - a half-made backup is never mistaken for the real one after a crash.
    """
    backup = job_dir / FOLLOW_UP_BACKUP_DIR_NAME
    staging = job_dir / f"{FOLLOW_UP_BACKUP_DIR_NAME}.tmp"
    shutil.rmtree(staging, ignore_errors=True)
    shutil.rmtree(backup, ignore_errors=True)
    staging.mkdir()
    for name in FOLLOW_UP_FILES:
        if (job_dir / name).is_file():
            shutil.copy2(job_dir / name, staging / name)
    os.replace(staging, backup)


def _copy_into_place(source: Path, target: Path) -> None:
    """Copy ``source`` over ``target`` in one step (copy beside it, then rename), so it is never half-written."""
    temporary = target.with_name(f"{target.name}.restoring")
    shutil.copy2(source, temporary)
    os.replace(temporary, target)


def _restore_settings(job_dir: Path, backup: Path) -> None:
    """Put meta.json's settings back as the backup has them (ablation changes the attribute mode).

    Only the settings: meta.json's status fields are the server's and are written separately.
    """
    if not (backup / META_JSON_NAME).is_file():
        return
    saved = read_meta(backup).get("settings")
    if isinstance(saved, dict):
        settings_from_dict(saved)  # raises on settings that could not be read back; nothing is written then
        update_meta(job_dir, settings=saved)


def restore_follow_up_files(job_dir: Path) -> bool:
    """Undo an unfinished follow-up: the report files and settings become what they were before it.

    Returns True when the job's files are as they were before the follow-up (also when there was no
    backup, because the task never started), False when putting them back failed.  The backup folder is
    removed only after everything is back, so a crash halfway through can simply run this again.
    """
    backup = job_dir / FOLLOW_UP_BACKUP_DIR_NAME
    shutil.rmtree(job_dir / f"{FOLLOW_UP_BACKUP_DIR_NAME}.tmp", ignore_errors=True)  # a backup never finished
    if not backup.is_dir():
        return True
    try:
        for name in REPORT_FILES:  # not meta.json: its status fields are the server's
            if (backup / name).is_file():
                _copy_into_place(backup / name, job_dir / name)
            else:  # the follow-up created it (attribution.json of a job that had none)
                (job_dir / name).unlink(missing_ok=True)
        _restore_settings(job_dir, backup)
    except (OSError, ValueError, KeyError, TypeError, AssertionError):
        return False
    discard_follow_up_backup(job_dir)
    return True


def discard_follow_up_backup(job_dir: Path) -> None:
    """Remove the backup once the follow-up has finished (or its files were put back)."""
    shutil.rmtree(job_dir / FOLLOW_UP_BACKUP_DIR_NAME, ignore_errors=True)


# --------------------------------------------------------------------------------------- store
class JobStore:
    """The in-memory index of jobs under ``<work_dir>/jobs``, rebuilt from disk when created."""

    def __init__(self, work_dir: Path) -> None:
        self.work_dir = Path(work_dir)
        self._lock = threading.RLock()
        self._records: dict[str, JobRecord] = {record.id: record for record in load_records(self.work_dir)}

    def create(self, url: str, settings: AuditSettings) -> JobRecord:
        """Make the directory and meta.json of a new queued job and add it to the index."""
        job_dir = new_job_dir(self.work_dir, url, settings)
        record = JobRecord(id=job_dir.name, job_dir=job_dir, url=url, created_at=utc_now_iso(), settings=settings)
        record.progress = Progress(0, record.total_frames, "waiting in the queue")
        write_meta(job_dir, new_meta(record))
        with self._lock:
            self._records[record.id] = record
        return record

    def get(self, job_id: str) -> JobRecord | None:
        with self._lock:
            record = self._records.get(job_id)
            if record is not None:
                self._refresh_unmanaged(record)
            return record

    def newest_first(self) -> list[JobRecord]:
        """All jobs, newest first (by creation time, then by id, which also starts with the time)."""
        with self._lock:
            for record in self._records.values():
                self._refresh_unmanaged(record)
            return sorted(self._records.values(), key=lambda r: (r.created_at, r.id), reverse=True)

    @staticmethod
    def _refresh_unmanaged(record: JobRecord) -> None:
        """A job another process is running (``flashtrace audit``): copy its status from its meta.json.

        That process, not the server, updates the file, so the index would otherwise show "running" until
        the next restart.  Jobs the server manages, or that have finished, are not read again.
        """
        if record.managed or not record.active:
            return
        fresh = record_from_disk(record.job_dir)
        if fresh is None:  # deleted or half-written right now; try again on the next request
            return
        record.status, record.phase, record.progress = fresh.status, fresh.phase, fresh.progress
        record.verdict, record.reason, record.error = fresh.verdict, fresh.reason, fresh.error
        record.settings = fresh.settings

    def remove(self, job_id: str) -> JobRecord | None:
        """Take a job out of the index (its directory is deleted by the caller or the runner)."""
        with self._lock:
            return self._records.pop(job_id, None)

    def summary(self, record: JobRecord, jobs_ahead: int | None = None) -> dict[str, Any]:
        """JobSummary built under the lock, so the runner cannot change the record halfway through."""
        with self._lock:
            return job_summary(record, jobs_ahead)

    # ------------------------------------------------------------------ state changes
    def queue_again(self, record: JobRecord) -> bool:
        """Queue a follow-up task (ablation or verification) on a finished job; False if it is busy."""
        with self._lock:
            if record.active:
                return False
            record.managed = True  # from now on the server writes this job's status (also a CLI job's)
            record.status, record.phase, record.error = "queued", "queued", None
            record.progress = Progress(0, 1, "waiting in the queue")
            save_state(record)
            return True

    def mark_running(self, record: JobRecord, phase: str, progress: Progress) -> None:
        with self._lock:
            record.status, record.phase, record.progress = "running", phase, progress
            save_state(record)

    def set_progress(self, record: JobRecord, phase: str, progress: Progress) -> None:
        """Per-frame progress lives in memory; meta.json is written only when the phase changes."""
        with self._lock:
            phase_changed = record.phase != phase
            record.phase, record.progress = phase, progress
            if phase_changed:
                save_state(record)

    def mark_done(self, record: JobRecord, report: Mapping[str, Any]) -> None:
        """The task finished: take verdict, reason, frame count and attribution mode from its report.

        The mode can change: after "Re-run attribution with ablation" the report says "ablate".
        """
        with self._lock:
            record.status, record.phase, record.error = "done", "done", None
            record.settings = dataclasses.replace(record.settings, attribute=str(report["settings"]["attribute"]))
            record.verdict, record.reason = str(report["verdict"]), str(report["reason"])
            record.frame_count = int(report["capture"]["frameCount"])
            record.progress = Progress(record.total_frames, record.total_frames, "done")
            save_state(record)

    def mark_follow_up_failed(self, record: JobRecord, message: str) -> None:
        """An ablation or verification failed or was stopped, and its report files were put back.

        The audit itself is still valid, so the job is "done" again (its report stays readable and the
        UI routes to it) and ``error`` says why the re-run did not finish.  Verdict, reason, settings and
        frame count were not changed by the task, so they are kept.
        """
        with self._lock:
            set_follow_up_failed(record, message)
            save_state(record)

    def mark_error(self, record: JobRecord, message: str) -> None:
        with self._lock:
            record.status, record.phase, record.error = "error", "error", message
            record.progress = Progress(record.progress.current, record.progress.total, "stopped with an error")
            save_state(record)

    def mark_cancelled(self, record: JobRecord) -> None:
        with self._lock:
            record.status = "cancelled"
            record.progress = Progress(record.progress.current, record.progress.total, "cancelled")
            save_state(record)
