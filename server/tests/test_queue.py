"""The runner (one job at a time, FIFO, cancelling) and the job index across restarts."""

from __future__ import annotations

import json
import signal
import threading
import time
from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cli import audit as cli_audit
from cli.jobs import AuditSettings, new_job_dir, read_meta, update_meta, write_meta
from cli.report import write_report
from server.app import build_uvicorn_server, create_app
from server.jobs import (
    FOLLOW_UP_BACKUP_DIR_NAME,
    RUN_DIR_NAME,
    STATUS_WRITER_KEY,
    STOPPED_BEFORE,
    STOPPED_DURING,
    JobStore,
    adopt_run,
    backup_follow_up_files,
    phase_for,
    queue_label,
    record_from_disk,
)
from server.runner import JobCancelled, JobProgress, Runner, Task, failure_sentence
from server.tests.fakes import (
    WAIT_TIMEOUT_S,
    FakePipeline,
    fake_report,
    load_report,
    save_report,
    submit,
    wait_for,
    wait_until_finished,
    wait_until_gone,
    write_frames,
)


def job_dir(work_dir: Path, job_id: str) -> Path:
    return work_dir / "jobs" / job_id


# ------------------------------------------------------------------------------------ FIFO
def test_one_job_at_a_time_in_submission_order(client: TestClient, pipeline: FakePipeline) -> None:
    pipeline.hold_audit.clear()
    first = submit(client, url="https://example.test/first")
    assert pipeline.audit_waiting.wait(30)
    second = submit(client, url="https://example.test/second")
    third = submit(client, url="https://example.test/third")
    assert client.get(f"/jobs/{first}").json()["status"] == "running"
    waiting = [client.get(f"/jobs/{job_id}").json() for job_id in (second, third)]
    assert [s["status"] for s in waiting] == ["queued", "queued"]
    assert [s["progress"]["label"] for s in waiting] == ["waiting for 1 job ahead", "waiting for 2 jobs ahead"]
    assert pipeline.audited == ["https://example.test/first"]  # nothing else started meanwhile
    pipeline.hold_audit.set()
    for job_id in (first, second, third):
        assert wait_until_finished(client, job_id)["status"] == "done"
    assert pipeline.audited == ["https://example.test/first", "https://example.test/second", "https://example.test/third"]


def test_queue_label() -> None:
    assert queue_label(0) == "starting"
    assert queue_label(1) == "waiting for 1 job ahead"
    assert queue_label(3) == "waiting for 3 jobs ahead"


# ------------------------------------------------------------------------------------ cancel
def test_cancel_while_queued(client: TestClient, pipeline: FakePipeline, work_dir: Path) -> None:
    pipeline.hold_audit.clear()
    first = submit(client, url="https://example.test/first")
    assert pipeline.audit_waiting.wait(30)
    second = submit(client, url="https://example.test/second")
    assert client.delete(f"/jobs/{second}").status_code == 204
    assert client.get(f"/jobs/{second}").status_code == 404
    assert not job_dir(work_dir, second).exists()
    pipeline.hold_audit.set()
    assert wait_until_finished(client, first)["status"] == "done"
    assert pipeline.audited == ["https://example.test/first"]  # the cancelled job never ran


def test_cancel_while_running_stops_the_capture_and_deletes_the_job(client: TestClient, pipeline: FakePipeline,
                                                                   work_dir: Path) -> None:
    pipeline.hold_audit.clear()  # never released: only cancelling can end this capture
    running = submit(client, url="https://example.test/running")
    assert pipeline.audit_waiting.wait(30)
    after = submit(client, url="https://example.test/after")
    assert client.delete(f"/jobs/{running}").status_code == 204
    assert client.get(f"/jobs/{running}").status_code == 404
    wait_until_gone(job_dir(work_dir, running))  # deleted by the runner once the capture stopped
    # The runner carries on with the next job (released so it can finish).
    wait_for(client, after, lambda s: s["status"] == "running")
    pipeline.hold_audit.set()
    assert wait_until_finished(client, after)["status"] == "done"


def test_cancel_during_ablation_stops_it_through_the_progress_hook(client: TestClient, pipeline: FakePipeline,
                                                                  work_dir: Path) -> None:
    job_id = submit(client)
    wait_until_finished(client, job_id)
    pipeline.hold_follow_up.clear()  # never released: only cancelling can end this ablation
    client.post(f"/jobs/{job_id}/ablate")
    wait_for(client, job_id, lambda s: s["phase"] == "ablating" and s["progress"]["total"] == 2)
    assert client.delete(f"/jobs/{job_id}").status_code == 204
    wait_until_gone(job_dir(work_dir, job_id))
    assert pipeline.ablated == []  # it never got past the held step


def test_cancel_during_a_step_without_progress_waits_for_it_then_deletes(client: TestClient, work_dir: Path,
                                                                        monkeypatch) -> None:
    from cli import audit as cli_audit

    job_id = submit(client)
    wait_until_finished(client, job_id)
    release = threading.Event()

    def slow_reattribute(job_dir_path: Path, mode: str = "ablate") -> dict:  # no progress parameter
        release.wait(30)
        return load_report(job_dir_path)

    monkeypatch.setattr(cli_audit, "reattribute", slow_reattribute)
    client.post(f"/jobs/{job_id}/ablate")
    wait_for(client, job_id, lambda s: s["phase"] == "ablating")
    assert client.delete(f"/jobs/{job_id}").status_code == 204
    assert job_dir(work_dir, job_id).exists()  # nothing calls back, so the step finishes first
    release.set()
    wait_until_gone(job_dir(work_dir, job_id))


def test_job_progress_maps_callbacks_to_phases(work_dir: Path) -> None:
    store = JobStore(work_dir)
    record = store.create("https://example.test/", AuditSettings(attribute="ablate"))
    progress = JobProgress(store, record)
    progress(5, 10)
    assert (record.phase, record.progress.label) == ("capturing", "frames 5/10")
    progress(10, 10)
    assert (record.phase, record.progress.label) == ("analyzing", "analysing frames")
    progress.phase("attributing (ablate)")
    assert (record.phase, record.progress.total) == ("attributing", 0)
    progress.phase("ablation 2: suppressed ['#a']", 2, 6)
    assert (record.phase, record.progress.current, record.progress.total) == ("ablating", 2, 6)
    verifying = JobProgress(store, record, fixed_phase="verifying")
    verifying.phase("verifying")
    verifying(3, 120)
    assert (record.phase, record.progress.label) == ("verifying", "frames 3/120")
    record.cancel.set()
    with pytest.raises(JobCancelled):
        progress(6, 10)
    with pytest.raises(JobCancelled):
        progress.phase("proposing fixes")


def test_shutdown_stops_the_running_job_as_error(work_dir: Path, pipeline: FakePipeline) -> None:
    store = JobStore(work_dir)
    runner = Runner(store)
    record = store.create("https://example.test/", AuditSettings())
    pipeline.hold_audit.clear()
    runner.submit(Task(record, "audit"))
    assert pipeline.audit_waiting.wait(30)
    runner.stop()
    assert record.status == "error" and record.error == STOPPED_DURING
    assert read_meta(record.job_dir)["status"] == "error"
    assert not (record.job_dir / RUN_DIR_NAME).exists()


def test_job_cancelled_is_not_an_exception_the_audit_would_swallow() -> None:
    """run_audit turns capture *Exceptions* into INCONCLUSIVE reports; a cancel must get past that."""
    assert not issubclass(JobCancelled, Exception)


def test_failure_sentence_is_one_short_sentence() -> None:
    assert failure_sentence("audit", RuntimeError("boom\nmore")) == "The audit failed (RuntimeError: boom)."
    assert failure_sentence("verify", KeyError()) == "Fix verification failed (KeyError)."
    long = failure_sentence("ablate", ValueError("x" * 1000))
    assert len(long) < 400 and long.endswith("...).")


# ------------------------------------------------------------------------------------ restart
def interrupted_job(work_dir: Path, status: str, phase: str) -> Path:
    """A server job directory whose meta.json says it was still queued/running (as after a crash)."""
    settings = AuditSettings(duration_s=2.0)
    path = new_job_dir(work_dir, f"https://example.test/{status}", settings)
    write_meta(path, {"jobId": path.name, "url": f"https://example.test/{status}", "settings": settings.to_dict(),
                      "createdAt": "2026-10-24T10:00:00Z", "status": status, "phase": phase, "verdict": None,
                      "reason": None, "error": None, "source": "server"})
    (path / RUN_DIR_NAME / "jobs").mkdir(parents=True)  # a half-built audit left behind
    return path


def test_restart_rebuilds_the_index_and_marks_interrupted_jobs_as_error(make_client, client: TestClient,
                                                                      work_dir: Path) -> None:
    done = submit(client)
    wait_until_finished(client, done)
    running = interrupted_job(work_dir, "running", "capturing")
    queued = interrupted_job(work_dir, "queued", "queued")

    restarted = make_client()
    jobs = {summary["id"]: summary for summary in restarted.get("/jobs").json()}
    assert set(jobs) == {done, running.name, queued.name}
    assert jobs[done]["status"] == "done" and jobs[done]["verdict"] == "FAIL"
    assert restarted.get(f"/jobs/{done}/report").status_code == 200
    assert jobs[running.name]["status"] == "error" and "server stopped during the job" in jobs[running.name]["error"]
    assert jobs[queued.name]["error"] == STOPPED_BEFORE
    assert read_meta(running)["status"] == "error"  # written back to disk
    assert not (running / RUN_DIR_NAME).exists()


@pytest.mark.parametrize("status, phase, message", [("running", "ablating", STOPPED_DURING),
                                                     ("queued", "queued", STOPPED_BEFORE)])
def test_interrupted_follow_up_leaves_the_job_done_with_its_report(make_client, client: TestClient, work_dir: Path,
                                                                    status: str, phase: str, message: str) -> None:
    """The audit is still valid: "done" (so the UI routes to the report), the stop explained in error."""
    job_id = submit(client)
    wait_until_finished(client, job_id)
    path = job_dir(work_dir, job_id)
    write_meta(path, {**read_meta(path), "status": status, "phase": phase})
    restarted = make_client()
    summary = restarted.get(f"/jobs/{job_id}").json()
    assert (summary["status"], summary["phase"], summary["error"]) == ("done", "done", message)
    assert summary["verdict"] == "FAIL"
    assert restarted.get(f"/jobs/{job_id}/report").status_code == 200
    assert read_meta(path)["status"] == "done"


def test_new_audit_cut_off_while_its_files_were_being_moved_in_is_still_an_error(make_client, work_dir: Path) -> None:
    """report.json alone does not make a job a finished audit: adopt_run may have stopped halfway."""
    path = interrupted_job(work_dir, "running", "attributing")
    write_report(fake_report("https://example.test/running", AuditSettings(duration_s=2.0), path), path / "report.json")
    summary = make_client().get(f"/jobs/{path.name}").json()
    assert (summary["status"], summary["phase"], summary["error"]) == ("error", "error", STOPPED_DURING)


def test_restart_puts_back_the_report_a_crashed_follow_up_had_half_rewritten(make_client, client: TestClient,
                                                                            work_dir: Path) -> None:
    job_id = submit(client)
    wait_until_finished(client, job_id)
    path = job_dir(work_dir, job_id)
    original = (path / "report.json").read_bytes()
    backup_follow_up_files(path)  # what the runner does before an ablation starts
    # The ablation got as far as writing a new report and the "ablate" setting, then the process died.
    rewritten = load_report(path)
    rewritten["culprits"], rewritten["fixes"] = [], []
    (path / "report.json").write_text(json.dumps(rewritten), encoding="utf-8")
    (path / "attribution.json").write_text("{}", encoding="utf-8")
    meta = read_meta(path)
    write_meta(path, {**meta, "status": "running", "phase": "ablating",
                      "settings": {**meta["settings"], "attribute": "ablate"}})

    summary = make_client().get(f"/jobs/{job_id}").json()
    assert (summary["status"], summary["error"]) == ("done", STOPPED_DURING)
    assert summary["settings"]["attribute"] == "bbox"
    assert (path / "report.json").read_bytes() == original
    assert not (path / "attribution.json").exists()  # the job had none before the ablation
    assert read_meta(path)["settings"]["attribute"] == "bbox"
    assert not (path / FOLLOW_UP_BACKUP_DIR_NAME).exists()


def test_cli_jobs_and_unreadable_folders(make_client, work_dir: Path) -> None:
    settings = AuditSettings(duration_s=2.0)
    cli_job = new_job_dir(work_dir, "https://example.test/cli", settings)
    report = fake_report("https://example.test/cli", settings, cli_job)
    write_report(report, cli_job / "report.json")
    write_frames(cli_job, 4)
    write_meta(cli_job, {"jobId": cli_job.name, "url": "https://example.test/cli", "settings": settings.to_dict(),
                         "status": "done", "createdAt": "2026-10-24T09:00:00Z", "verdict": "FAIL", "reason": "r"})
    broken = new_job_dir(work_dir, "https://example.test/broken", settings)  # no meta.json at all
    client = make_client()
    ids = [summary["id"] for summary in client.get("/jobs").json()]
    assert ids == [cli_job.name] and broken.name not in ids
    summary = client.get(f"/jobs/{cli_job.name}").json()
    assert summary["phase"] == "done" and summary["verdict"] == "FAIL"
    assert client.get(f"/jobs/{cli_job.name}/frames/3.png").status_code == 200


# ------------------------------------------------------------------------------------ adopt_run
def test_adopt_run_moves_the_audit_up_and_keeps_the_job_id(work_dir: Path, pipeline: FakePipeline) -> None:
    store = JobStore(work_dir)
    record = store.create("https://example.test/", AuditSettings(duration_s=2.0))
    _report, audit_dir, _code = pipeline.run_audit(record.url, record.settings, work_dir=record.job_dir / RUN_DIR_NAME)
    adopt_run(record.job_dir, audit_dir)
    meta = read_meta(record.job_dir)
    assert meta["jobId"] == record.id and meta["auditJobId"] == audit_dir.name and meta["status"] == "done"
    assert meta["jobDir"] == str(record.job_dir.resolve())  # no longer the staging folder
    assert (record.job_dir / "report.json").is_file() and (record.job_dir / "frames" / "000003.png").is_file()
    assert not (record.job_dir / RUN_DIR_NAME).exists()


# ------------------------------------------------------------------------------------ Ctrl-C
def wait_until(condition: Callable[[], bool], timeout: float = WAIT_TIMEOUT_S) -> None:
    """Poll ``condition`` until it holds; fail after ``timeout`` seconds."""
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert condition(), "the condition was not reached in time"


def test_ctrl_c_handler_stops_the_running_job_at_once_and_starts_no_queued_one(work_dir: Path, project: Path,
                                                                             pipeline: FakePipeline,
                                                                             tmp_path: Path) -> None:
    """The stop flag is set by the signal handler, not later by the lifespan (which runs after uvicorn
    has closed its connections, when a killed Chromium has long been reported as a finished audit)."""
    app = create_app(work_dir, project, static_dir=tmp_path / "no-ui-build")
    store, runner = app.state.server.store, app.state.server.runner
    running = store.create("https://example.test/running", AuditSettings(duration_s=2.0))
    queued = store.create("https://example.test/queued", AuditSettings(duration_s=2.0))
    pipeline.hold_audit.clear()  # never released: only the stop can end this capture
    runner.submit(Task(running, "audit"))
    assert pipeline.audit_waiting.wait(30)
    runner.submit(Task(queued, "audit"))

    server = build_uvicorn_server(app, "127.0.0.1", 8001)
    server.handle_exit(signal.SIGINT, None)  # what uvicorn calls when Ctrl-C arrives
    assert server.should_exit
    wait_until(lambda: running.status == "error")  # before runner.stop() (the lifespan) was ever called
    assert running.error == STOPPED_DURING
    runner.stop()
    assert queued.status == "queued" and read_meta(queued.job_dir)["status"] == "queued"
    assert pipeline.audited == ["https://example.test/running"]


@pytest.mark.parametrize("ending", ["inconclusive report", "exception"])
def test_capture_killed_by_ctrl_c_is_stopped_during_not_a_finished_audit(work_dir: Path, pipeline: FakePipeline,
                                                                       monkeypatch: pytest.MonkeyPatch,
                                                                       ending: str) -> None:
    """Ctrl-C kills Chromium too.  cli.audit turns the failed capture into an INCONCLUSIVE report (or a
    later step raises) without calling the progress callback again; neither may count as a result."""
    started, chromium_died = threading.Event(), threading.Event()

    def killed_capture(target: str, settings: AuditSettings, work_dir: Path | None = None, **_ignored):
        started.set()
        chromium_died.wait(30)
        if ending == "exception":
            raise RuntimeError("Target page, context or browser has been closed")
        return pipeline.run_audit(target, settings, work_dir=work_dir)  # a complete report, no callbacks

    monkeypatch.setattr(cli_audit, "run_audit", killed_capture)
    store = JobStore(work_dir)
    runner = Runner(store)
    record = store.create("https://example.test/", AuditSettings(duration_s=2.0))
    runner.submit(Task(record, "audit"))
    assert started.wait(30)
    runner.begin_shutdown()
    chromium_died.set()
    runner.stop()
    assert (record.status, record.error, record.verdict) == ("error", STOPPED_DURING, None)
    meta = read_meta(record.job_dir)
    assert (meta["status"], meta["error"], meta["verdict"]) == ("error", STOPPED_DURING, None)
    assert not (record.job_dir / "report.json").exists()  # the staged result was not adopted
    assert not (record.job_dir / RUN_DIR_NAME).exists()


# ------------------------------------------------------------------------------------ follow-ups
def test_ctrl_c_during_verification_keeps_the_report_as_it_was(client: TestClient, work_dir: Path,
                                                              monkeypatch: pytest.MonkeyPatch) -> None:
    """capture/fix.py records a dead browser as a failed verification; after Ctrl-C that is not kept."""
    job_id = submit(client)
    wait_until_finished(client, job_id)
    path = job_dir(work_dir, job_id)
    original = (path / "report.json").read_bytes()
    started, chromium_died = threading.Event(), threading.Event()

    def verification_with_a_killed_browser(job_dir_path: Path, n: int, progress=None) -> dict:
        started.set()
        chromium_died.wait(30)
        report = load_report(job_dir_path)
        report["fixes"][n]["rationale"] += " Verification capture failed: TargetClosedError."
        return save_report(job_dir_path, report)

    monkeypatch.setattr(cli_audit, "verify_fix_in_job", verification_with_a_killed_browser)
    assert client.post(f"/jobs/{job_id}/fixes/0/verify").status_code == 202
    assert started.wait(30)
    client.app.state.server.runner.begin_shutdown()
    chromium_died.set()
    summary = wait_until_finished(client, job_id)
    assert (summary["status"], summary["phase"], summary["error"]) == ("done", "done", STOPPED_DURING)
    assert (path / "report.json").read_bytes() == original
    assert not (path / FOLLOW_UP_BACKUP_DIR_NAME).exists()


def test_ablation_that_rewrote_the_report_and_then_failed_is_undone(client: TestClient, work_dir: Path,
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    job_id = submit(client)
    wait_until_finished(client, job_id)
    path = job_dir(work_dir, job_id)
    original = {name: (path / name).read_bytes() for name in ("report.json", "report.html")}

    def half_done_ablation(job_dir_path: Path, mode: str = "ablate") -> dict:
        report = load_report(job_dir_path)
        report["culprits"], report["fixes"] = [], []
        save_report(job_dir_path, report)
        (job_dir_path / "report.html").write_text("<p>partial</p>", encoding="utf-8")
        (job_dir_path / "attribution.json").write_text("{}", encoding="utf-8")
        update_meta(job_dir_path, settings={**read_meta(job_dir_path)["settings"], "attribute": "ablate"})
        raise OSError("disk full")

    monkeypatch.setattr(cli_audit, "reattribute", half_done_ablation)
    client.post(f"/jobs/{job_id}/ablate")
    summary = wait_until_finished(client, job_id)
    assert (summary["status"], summary["phase"]) == ("done", "done")
    assert summary["error"] == "Ablation failed (OSError: disk full)."
    assert summary["settings"]["attribute"] == "bbox" and read_meta(path)["settings"]["attribute"] == "bbox"
    assert {name: (path / name).read_bytes() for name in original} == original
    assert not (path / "attribution.json").exists()
    assert client.get(f"/jobs/{job_id}/report").status_code == 200


def test_successful_follow_up_after_a_failed_one_clears_the_error(client: TestClient, pipeline: FakePipeline,
                                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    job_id = submit(client)
    wait_until_finished(client, job_id)
    good = cli_audit.verify_fix_in_job

    def broken(job_dir_path: Path, n: int, progress=None) -> dict:
        raise RuntimeError("chromium could not start")

    monkeypatch.setattr(cli_audit, "verify_fix_in_job", broken)
    client.post(f"/jobs/{job_id}/fixes/0/verify")
    failed = wait_until_finished(client, job_id)
    assert failed["status"] == "done" and failed["error"].startswith("Fix verification failed (RuntimeError")
    monkeypatch.setattr(cli_audit, "verify_fix_in_job", good)
    client.post(f"/jobs/{job_id}/fixes/0/verify")
    again = wait_until_finished(client, job_id)
    assert (again["status"], again["error"]) == ("done", None)
    assert client.get(f"/jobs/{job_id}/report").json()["fixes"][0]["verified"] is True


# ------------------------------------------------------------------------------------ CLI jobs
def cli_job_started(work_dir: Path) -> Path:
    """A job as cli.audit._start_job leaves it while `flashtrace audit` runs: status running, no phase."""
    settings = AuditSettings(duration_s=2.0)
    path = new_job_dir(work_dir, "https://example.test/cli", settings)
    write_meta(path, {"jobId": path.name, "jobDir": str(path.resolve()), "url": "https://example.test/cli",
                      "settings": settings.to_dict(), "status": "running", "createdAt": "2026-10-24T09:00:00Z",
                      "verdict": None})
    return path


def finish_cli_job(path: Path) -> None:
    """What run_audit writes when the CLI audit ends: frames, the report, then status/verdict/reason."""
    settings = AuditSettings(duration_s=2.0)
    write_frames(path, 4)
    write_report(fake_report("https://example.test/cli", settings, path), path / "report.json")
    update_meta(path, status="done", verdict="FAIL", reason="r", finishedAt="2026-10-24T09:00:05Z")


def test_cli_audit_running_while_the_server_starts_is_left_alone(make_client, work_dir: Path) -> None:
    cli_job = cli_job_started(work_dir)
    before = read_meta(cli_job)
    client = make_client()
    summary = client.get(f"/jobs/{cli_job.name}").json()
    assert (summary["status"], summary["phase"], summary["error"]) == ("running", "capturing", None)
    assert read_meta(cli_job) == before  # the server did not write into the CLI's job

    finish_cli_job(cli_job)
    summary = client.get(f"/jobs/{cli_job.name}").json()  # re-read from disk while it was running
    assert (summary["status"], summary["phase"], summary["error"], summary["verdict"]) == ("done", "done", None, "FAIL")
    assert [s["status"] for s in client.get("/jobs").json()] == ["done"]
    assert client.get(f"/jobs/{cli_job.name}/report").status_code == 200
    assert "phase" not in read_meta(cli_job) and "error" not in read_meta(cli_job)

    restarted = make_client().get(f"/jobs/{cli_job.name}").json()
    assert (restarted["status"], restarted["phase"], restarted["error"]) == ("done", "done", None)


def test_follow_up_on_a_cli_job_makes_it_the_servers_to_recover(make_client, work_dir: Path) -> None:
    cli_job = cli_job_started(work_dir)
    finish_cli_job(cli_job)
    client = make_client()
    assert client.post(f"/jobs/{cli_job.name}/ablate").status_code == 202
    assert wait_until_finished(client, cli_job.name)["status"] == "done"
    assert read_meta(cli_job)[STATUS_WRITER_KEY] == "server"
    # The server dies during a second ablation: on the next start it is the server's job to recover.
    write_meta(cli_job, {**read_meta(cli_job), "status": "running", "phase": "ablating"})
    summary = make_client().get(f"/jobs/{cli_job.name}").json()
    assert (summary["status"], summary["error"]) == ("done", STOPPED_DURING)


@pytest.mark.parametrize("status, stored, shown", [
    ("done", "error", "done"),          # left behind by the old recovery of a CLI job
    ("done", "ablating", "done"),
    ("running", "done", "capturing"),
    ("running", "verifying", "verifying"),
    ("error", "capturing", "error"),
    ("queued", "capturing", "queued"),
    ("cancelled", "capturing", "capturing"),  # a cancelled job keeps the phase it was stopped in
    ("done", None, "done"),
])
def test_phase_that_contradicts_the_status_is_replaced(status: str, stored: str | None, shown: str) -> None:
    assert phase_for(status, stored) == shown


def test_record_from_disk_never_shows_done_with_phase_error(work_dir: Path) -> None:
    path = cli_job_started(work_dir)
    finish_cli_job(path)
    update_meta(path, phase="error", error=STOPPED_DURING)  # what the old recovery left in a CLI job
    record = record_from_disk(path)
    assert record is not None and (record.status, record.phase) == ("done", "done")
