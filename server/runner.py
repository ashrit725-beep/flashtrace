"""The job runner: one background thread that runs jobs one at a time, first in, first out.

docs/API.md: "one job runs at a time (others wait in a FIFO queue)".  An audit drives Chromium through
Playwright's synchronous API, which blocks while it works, so the runner is a single ordinary thread
that takes the oldest task from a queue, runs it to the end, and then takes the next one.

Three kinds of task share the queue:

* ``audit``  - a new job: ``cli.audit.run_audit`` (capture, analysis, attribution, report);
* ``ablate`` - ``cli.audit.reattribute(job_dir, mode="ablate")`` on a finished job;
* ``verify`` - ``cli.audit.verify_fix_in_job(job_dir, n)`` on a finished job.

Any exception ends an audit with status "error" and a one-sentence message; the thread carries on.
A follow-up (ablate, verify) works on an audit that is already done, so it never takes that audit
away: the report files are backed up before it starts and put back if it fails or is stopped, and the
job stays "done" with the message in ``error`` (server/jobs.py, ``mark_follow_up_failed``).

Cancelling (DELETE /jobs/{id}): a queued task is simply taken out of the queue.  A running task is
stopped from the inside: the capture calls our progress object once per frame (and cli.audit calls its
``phase`` hook between steps), and both raise ``JobCancelled`` as soon as the job has been cancelled.
``JobCancelled`` derives from BaseException (like KeyboardInterrupt), so the audit's own handler that
turns a capture failure into an INCONCLUSIVE report (it catches Exception) lets it through, and the
capture's ``finally`` blocks close the page and the browser on the way out.  A step that never calls
back finishes first; the runner then deletes the job's directory.

Stopping the server (Ctrl-C): ``begin_shutdown`` runs the moment the signal arrives (app.serve hooks it
into uvicorn's signal handler).  A terminal's Ctrl-C also reaches Chromium, which dies at once; the
capture then fails and cli.audit turns that into an INCONCLUSIVE report *without* calling back.  So a
task's result is checked against the cancel flag once more before it is kept: a result that comes back
after the stop began is not a result, and the job ends as "The server stopped during the job.".
"""

from __future__ import annotations

import inspect
import shutil
import threading
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from cli import audit as cli_audit
from cli.jobs import REPORT_JSON_NAME
from server.jobs import (
    RUN_DIR_NAME,
    STOPPED_DURING,
    JobRecord,
    JobStore,
    Progress,
    adopt_run,
    backup_follow_up_files,
    delete_job_files,
    discard_follow_up_backup,
    restore_follow_up_files,
)

TASK_KINDS: tuple[str, ...] = ("audit", "ablate", "verify")
FOLLOW_UP_KINDS: tuple[str, ...] = ("ablate", "verify")  # tasks on a job that is already done
FAILURE_PREFIX = {"audit": "The audit failed", "ablate": "Ablation failed", "verify": "Fix verification failed"}
MAX_DETAIL_CHARS = 300  # error messages are cut here so the UI shows one short sentence
STOP_TIMEOUT_S = 10.0  # how long shutdown waits for the running job to reach its next frame


class JobCancelled(BaseException):
    """Raised inside a running job's progress callback to stop it (see the module docstring)."""


def stop_if_cancelled(record: JobRecord) -> None:
    """Raise JobCancelled when the user deleted the job or the server is stopping."""
    if record.cancel.is_set():
        raise JobCancelled()


@dataclass(frozen=True)
class Task:
    """One unit of work for the runner: which job, what to do, and for "verify" which fix."""

    record: JobRecord
    kind: str
    fix_index: int | None = None


def failure_sentence(kind: str, exc: BaseException) -> str:
    """'The audit failed (TimeoutError: the page did not load).' - one sentence for the user."""
    text = str(exc).strip()
    first_line = text.splitlines()[0].strip().rstrip(".") if text else ""  # our sentence adds its own full stop
    detail = f"{type(exc).__name__}: {first_line}" if first_line else type(exc).__name__
    if len(detail) > MAX_DETAIL_CHARS:
        detail = detail[: MAX_DETAIL_CHARS - 3].rstrip() + "..."
    return f"{FAILURE_PREFIX[kind]} ({detail})."


def audit_function(name: str) -> Callable[..., Any]:
    """``cli.audit.<name>``, looked up at call time (so tests can replace it) and checked to exist."""
    function = getattr(cli_audit, name, None)
    if not callable(function):
        raise RuntimeError(f"cli.audit.{name} is not available in this build")
    return function


# --------------------------------------------------------------------------------------- tasks
class JobProgress:
    """The progress callback of one task.

    ``progress(done, total)`` is called by the capture once per frame.  ``progress.phase(label, done,
    total)`` is the optional hook through which cli.audit reports its later steps (attribution, fix
    proposals, verification).  Both update the job's phase and progress, and both raise JobCancelled
    once the job has been cancelled: that is how a running job is stopped.

    ``fixed_phase`` is set for follow-up tasks ("ablating", "verifying"): their whole run keeps that one
    phase, as the web UI expects, and only the progress numbers and label change.
    """

    def __init__(self, store: JobStore, record: JobRecord, fixed_phase: str | None = None) -> None:
        self.store, self.record, self.fixed_phase = store, record, fixed_phase

    def __call__(self, done: int, total: int) -> None:
        self._stop_if_cancelled()
        if self.fixed_phase is not None:  # the re-capture of a verification
            phase, label = self.fixed_phase, f"frames {done}/{total}"
        elif done >= total:  # every frame is in; run_audit analyses them next
            phase, label = "analyzing", "analysing frames"
        else:
            phase, label = "capturing", f"frames {done}/{total}"
        self.store.set_progress(self.record, phase, Progress(done, total, label))

    def phase(self, label: str, done: int | None = None, total: int | None = None) -> None:
        self._stop_if_cancelled()
        counted = done is not None and total is not None
        phase = self.fixed_phase or self._later_audit_phase(counted)
        # A step without counts reports total 0; the UI then shows no progress bar for it.
        progress = Progress(int(done), int(total), label) if counted else Progress(0, 0, label)  # type: ignore[arg-type]
        self.store.set_progress(self.record, phase, progress)

    def _later_audit_phase(self, counted: bool) -> str:
        """In a new audit, attribution's counted steps are ablation re-captures when the mode is "ablate"."""
        return "ablating" if counted and self.record.settings.attribute == "ablate" else "attributing"

    def _stop_if_cancelled(self) -> None:
        stop_if_cancelled(self.record)


def call_with_progress(function: Callable[..., Any], *args: Any, progress: JobProgress, **kwargs: Any) -> Any:
    """Call ``function``, passing ``progress=`` only when it accepts that argument."""
    if "progress" in inspect.signature(function).parameters:
        kwargs["progress"] = progress
    return function(*args, **kwargs)


def run_audit_task(store: JobStore, task: Task) -> None:
    """A new job: run_audit builds the job in <job>/run/, then its files are moved into <job>/."""
    record = task.record
    store.mark_running(record, "capturing", Progress(0, record.total_frames, "starting the browser"))
    staging = record.job_dir / RUN_DIR_NAME
    try:
        report, audit_dir, _exit_code = audit_function("run_audit")(
            record.url, record.settings, work_dir=staging, progress=JobProgress(store, record)
        )
        # A capture killed by Ctrl-C comes back as an INCONCLUSIVE report: once the job was cancelled
        # or the server began to stop, the result is not kept (see the module docstring).
        stop_if_cancelled(record)
        adopt_run(record.job_dir, audit_dir)
    finally:
        shutil.rmtree(staging, ignore_errors=True)  # nothing is left behind, also after an error
    store.mark_done(record, report)


def run_ablation_task(store: JobStore, task: Task) -> None:
    """Re-run attribution with ablation on a finished job (replaces the report's culprits and fixes)."""
    record = task.record
    store.mark_running(record, "ablating", Progress(0, 0, "re-capturing with each candidate suppressed"))
    progress = JobProgress(store, record, fixed_phase="ablating")
    report = call_with_progress(audit_function("reattribute"), record.job_dir, mode="ablate", progress=progress)
    stop_if_cancelled(record)  # re-captures that failed because the server is stopping are not results
    store.mark_done(record, report)


def run_verification_task(store: JobStore, task: Task) -> None:
    """Re-capture with report.fixes[n] applied and record the measured result in the report."""
    record = task.record
    store.mark_running(record, "verifying", Progress(0, 0, f"re-capturing with fix {task.fix_index} applied"))
    progress = JobProgress(store, record, fixed_phase="verifying")
    report = call_with_progress(audit_function("verify_fix_in_job"), record.job_dir, task.fix_index, progress=progress)
    stop_if_cancelled(record)  # a verification capture killed by Ctrl-C is not a measurement
    store.mark_done(record, report)


TASK_FUNCTIONS: dict[str, Callable[[JobStore, Task], None]] = {
    "audit": run_audit_task,
    "ablate": run_ablation_task,
    "verify": run_verification_task,
}


def run_task(store: JobStore, task: Task) -> None:
    """Run one task and record how it ended: done, cancelled, or with a one-sentence error message."""
    record = task.record
    follow_up = task.kind in FOLLOW_UP_KINDS
    try:
        stop_if_cancelled(record)
        if follow_up:
            backup_follow_up_files(record.job_dir)  # put back by end_unfinished_task if it does not finish
        TASK_FUNCTIONS[task.kind](store, task)
    except JobCancelled:
        if record.stopped_by_shutdown:
            end_unfinished_task(store, task, STOPPED_DURING)
        else:
            store.mark_cancelled(record)  # the user deleted the job; the runner removes its folder next
    except Exception as exc:  # noqa: BLE001 - every failure must end the task, never stop the thread
        # While the server is stopping, a failure is a symptom of the stop (Chromium was killed too).
        message = STOPPED_DURING if record.stopped_by_shutdown else failure_sentence(task.kind, exc)
        end_unfinished_task(store, task, message)
    finally:
        if follow_up:
            discard_follow_up_backup(record.job_dir)


def end_unfinished_task(store: JobStore, task: Task, message: str) -> None:
    """Record a task that failed or was stopped by the server shutting down.

    A new audit becomes "error".  A follow-up gets its report files put back, and the job stays "done"
    with ``message`` in ``error`` - unless they could not be put back, then it is "error" as well.
    """
    record = task.record
    if task.kind in FOLLOW_UP_KINDS and restore_follow_up_files(record.job_dir) \
            and (record.job_dir / REPORT_JSON_NAME).is_file():
        store.mark_follow_up_failed(record, message)
    else:
        store.mark_error(record, message)


# --------------------------------------------------------------------------------------- runner
class Runner:
    """One worker thread and its FIFO queue of tasks.  The thread starts with the first task."""

    def __init__(self, store: JobStore) -> None:
        self.store = store
        self._queue: deque[Task] = deque()
        self._condition = threading.Condition()  # guards the queue, _current and _stopping
        self._current: Task | None = None
        self._stopping = False
        self._thread: threading.Thread | None = None

    def submit(self, task: Task) -> None:
        """Add a task at the back of the queue."""
        if task.kind not in TASK_KINDS:
            raise ValueError(f"unknown task kind {task.kind!r}")
        with self._condition:
            self._queue.append(task)
            if self._thread is None:
                self._thread = threading.Thread(target=self._work, name="flashtrace-runner", daemon=True)
                self._thread.start()
            self._condition.notify()

    def jobs_ahead(self, job_id: str) -> int | None:
        """How many tasks run before this job's queued task (the running one included); None if not queued."""
        with self._condition:
            waiting = [task.record.id for task in self._queue]
            if job_id not in waiting:
                return None
            return waiting.index(job_id) + (1 if self._current is not None else 0)

    def cancel(self, record: JobRecord) -> bool:
        """Stop all work for ``record``.  True when the runner is busy with it right now.

        In that case the runner deletes the job's directory itself once the task has stopped, so the
        directory is never removed while the audit is still writing into it.
        """
        with self._condition:
            self._queue = deque(task for task in self._queue if task.record is not record)
            running = self._current is not None and self._current.record is record
            if running:
                record.delete_when_stopped = True
                record.cancel.set()
            return running

    def begin_shutdown(self) -> None:
        """The server is stopping: start no new task, and stop the running one at its next callback.

        Called from the Ctrl-C handler (app.serve), before uvicorn closes its connections, and again by
        ``stop``; calling it twice does no harm.  Queued tasks stay queued; the next start marks them.
        """
        with self._condition:
            self._stopping = True
            if self._current is not None:
                self._current.record.stopped_by_shutdown = True
                self._current.record.cancel.set()
            self._condition.notify_all()

    def stop(self, timeout: float = STOP_TIMEOUT_S) -> None:
        """Shut down: begin_shutdown, then wait (at most ``timeout`` seconds) for the thread to end."""
        self.begin_shutdown()
        with self._condition:
            thread = self._thread
        if thread is not None:
            thread.join(timeout)

    def _work(self) -> None:
        """Thread body: take the oldest task, run it, repeat until stop()."""
        while True:
            task = self._next_task()
            if task is None:
                return
            run_task(self.store, task)
            with self._condition:
                self._current = None
            if task.record.delete_when_stopped:
                delete_job_files(task.record.job_dir)

    def _next_task(self) -> Task | None:
        """Wait for a task and mark it as the current one; None once the runner is stopping."""
        with self._condition:
            while not self._queue and not self._stopping:
                self._condition.wait()
            if self._stopping:
                return None
            self._current = self._queue.popleft()
            return self._current
