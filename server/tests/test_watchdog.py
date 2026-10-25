"""A page that hangs its browser must not hold the runner: server/processes.py and runner.Watchdog.

The stand-in for a hung Playwright call starts a "driver" process (a child of the test process) whose
own child, the "browser", is a grandchild like Chromium.  The call waits for the driver without ever
calling back, and raises once the driver ends - which happens when the browser is killed, just as
Playwright raises TargetClosedError when Chromium dies.  The browser tests at the end do the same with
a real page whose script loops forever.
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from cli import audit as cli_audit
from cli.jobs import AuditSettings
from server import processes
from server import runner as runner_module
from server.app import create_app
from server.jobs import STOPPED_DURING, JobStore
from server.processes import browser_processes, descendants, kill_processes, process_table
from server.runner import Runner, Task, Watchdog, stall_sentence
from server.tests.conftest import BASE_URL
from server.tests.fakes import FakePipeline, load_report, submit, wait_for, wait_until_finished, wait_until_gone

BROWSER_LIFETIME_S = 60  # a fake browser that is never killed ends by itself, so a failed test leaks nothing
HUNG_DRIVER = (
    "import subprocess, sys\n"
    f"browser = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep({BROWSER_LIFETIME_S})'])\n"
    "browser.wait()\n"
)
FAST_GRACE_S = 0.2


class HungBrowser:
    """A browser call that hangs (see the module docstring); ``hang_in`` picks the URLs that hang."""

    def __init__(self, pipeline: FakePipeline, hang_in: str = "hang") -> None:
        self.pipeline, self.hang_in = pipeline, hang_in
        self.started = threading.Event()
        self.drivers: list[subprocess.Popen[bytes]] = []

    def hang(self) -> None:
        driver = subprocess.Popen([sys.executable, "-c", HUNG_DRIVER])
        self.drivers.append(driver)
        self.started.set()
        driver.wait()  # no progress callback while the "browser" lives
        raise RuntimeError("Target page, context or browser has been closed")

    def run_audit(self, target: str, settings: AuditSettings, work_dir: Path | None = None,
                  progress: Callable[[int, int], None] | None = None, **ignored: Any) -> Any:
        if self.hang_in not in target:
            return self.pipeline.run_audit(target, settings, work_dir=work_dir, progress=progress, **ignored)
        if progress is not None:
            progress(1, 120)  # one frame, then the page's script never yields again
        self.hang()

    def verify_fix_in_job(self, job_dir: Path, n: int, progress: Any = None) -> dict[str, Any]:
        self.hang()
        return {}  # never reached

    def cleanup(self) -> None:
        for driver in self.drivers:
            if driver.poll() is None:
                driver.kill()


@pytest.fixture
def hung(pipeline: FakePipeline, monkeypatch: pytest.MonkeyPatch) -> Iterator[HungBrowser]:
    fake = HungBrowser(pipeline)
    before = browser_processes()
    monkeypatch.setattr(cli_audit, "run_audit", fake.run_audit)
    yield fake
    kill_processes(browser_processes() - before)  # only if a test failed before its browser was killed
    fake.cleanup()


def wait_until(condition: Callable[[], bool], timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert condition(), "the condition was not reached in time"


# ------------------------------------------------------------------------------------ processes
def test_descendants_gives_each_process_below_the_root_its_depth() -> None:
    table = [(0, 0), (1, 0), (10, 1), (11, 10), (12, 11), (13, 11), (20, 2), (21, 20)]
    assert descendants(1, table) == {10: 1, 11: 2, 12: 3, 13: 3}
    assert descendants(99, table) == {}


def test_without_ps_nothing_is_found_and_nothing_is_killed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(processes, "PS_COMMAND", ("flashtrace-no-such-program",))
    assert process_table() is None
    assert browser_processes() == set()


def test_browser_processes_are_the_grandchildren_and_killing_them_ends_the_driver() -> None:
    before = browser_processes()
    driver = subprocess.Popen([sys.executable, "-c", HUNG_DRIVER])
    try:
        wait_until(lambda: bool(browser_processes() - before))
        browsers = browser_processes() - before
        assert driver.pid not in browsers  # the driver (our child) is left alone
        assert kill_processes(browsers) == len(browsers)
        assert driver.wait(timeout=10) == 0  # its browser died, so the driver ended
    finally:
        if driver.poll() is None:
            driver.kill()


# ------------------------------------------------------------------------------------ Watchdog rules
@pytest.fixture
def fake_processes(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Replace process listing and killing in server.runner with a controllable table."""
    state: dict[str, Any] = {"browsers": set(), "killed": []}
    monkeypatch.setattr(runner_module, "browser_processes", lambda: set(state["browsers"]))
    monkeypatch.setattr(runner_module, "kill_processes", lambda pids: state["killed"].append(set(pids)) or len(pids))
    return state


def watched(work_dir: Path, earlier: set[int], start: float) -> Watchdog:
    record = JobStore(work_dir).create("https://example.test/", AuditSettings())
    watchdog = Watchdog(record, stall_limit_s=10.0, grace_s=2.0)
    watchdog.earlier_browsers = set(earlier)
    record.last_callback = watchdog.stall_checked_at = start
    return watchdog


def test_watchdog_kills_a_cancelled_tasks_own_browser_after_the_grace(work_dir: Path,
                                                                     fake_processes: dict[str, Any]) -> None:
    fake_processes["browsers"] = {7, 8, 9}
    watchdog = watched(work_dir, earlier={7}, start=100.0)
    watchdog.check(101.0)
    assert fake_processes["killed"] == []  # not cancelled, not silent for long
    watchdog.record.cancel.set()
    watchdog.check(102.0)  # the cancel is seen here
    watchdog.check(103.9)
    assert fake_processes["killed"] == []  # still within the grace: a normal task stops at its next frame
    watchdog.check(104.0)
    watchdog.check(104.5)  # and again, in case a fall-through started another browser
    assert fake_processes["killed"] == [{8, 9}, {8, 9}]  # never process 7, which was there before the task
    assert watchdog.record.timed_out_after is None


def test_watchdog_stops_a_silent_task_only_while_it_has_a_browser(work_dir: Path,
                                                                  fake_processes: dict[str, Any]) -> None:
    watchdog = watched(work_dir, earlier=set(), start=100.0)
    watchdog.check(109.9)
    watchdog.check(110.0)  # 10 s silent, but no browser: computation, which always ends
    assert fake_processes["killed"] == [] and not watchdog.record.cancel.is_set()
    fake_processes["browsers"] = {5}
    watchdog.check(119.9)  # the next look comes a full limit after the last one
    assert fake_processes["killed"] == []
    watchdog.record.last_callback = 115.0  # a callback resets the silence
    watchdog.check(120.0)
    assert fake_processes["killed"] == []
    watchdog.check(125.0)  # 10 s since that callback, with a browser of its own: hung
    assert fake_processes["killed"] == [{5}]
    assert watchdog.record.cancel.is_set() and watchdog.record.timed_out_after == 10.0


# ------------------------------------------------------------------------------------ through the API
def test_delete_stops_a_task_hung_in_its_browser_and_the_next_job_runs(client: TestClient, hung: HungBrowser,
                                                                      work_dir: Path) -> None:
    client.app.state.server.runner.cancel_grace_s = FAST_GRACE_S
    stuck = submit(client, url="https://example.test/hang")
    assert hung.started.wait(30)
    after = submit(client, url="https://example.test/after")
    assert client.delete(f"/jobs/{stuck}").status_code == 204
    wait_until_gone(work_dir / "jobs" / stuck, timeout=15)  # the runner let go of it: the browser was killed
    assert wait_until_finished(client, after)["status"] == "done"
    assert hung.drivers[0].wait(timeout=10) == 0


def test_a_task_silent_with_a_browser_for_the_stall_limit_ends_as_error(client: TestClient,
                                                                       hung: HungBrowser) -> None:
    client.app.state.server.runner.stall_limit_s = 1.0
    stuck = submit(client, url="https://example.test/hang")
    after = submit(client, url="https://example.test/after")
    summary = wait_until_finished(client, stuck)
    assert (summary["status"], summary["error"]) == ("error", stall_sentence(1.0))
    assert wait_until_finished(client, after)["status"] == "done"


def test_a_follow_up_after_one_that_timed_out_starts_afresh(client: TestClient, hung: HungBrowser,
                                                            pipeline: FakePipeline, work_dir: Path,
                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    job_id = submit(client)
    wait_until_finished(client, job_id)
    report_before = load_report(work_dir / "jobs" / job_id)
    client.app.state.server.runner.stall_limit_s = 1.0
    monkeypatch.setattr(cli_audit, "verify_fix_in_job", hung.verify_fix_in_job)
    assert client.post(f"/jobs/{job_id}/fixes/0/verify").status_code == 202
    summary = wait_for(client, job_id, lambda s: s["status"] == "done" and s["error"] is not None)
    assert summary["error"] == stall_sentence(1.0)  # the audit itself is intact; the re-run did not finish
    assert load_report(work_dir / "jobs" / job_id) == report_before
    monkeypatch.setattr(cli_audit, "verify_fix_in_job", pipeline.verify_fix_in_job)
    assert client.post(f"/jobs/{job_id}/fixes/0/verify").status_code == 202
    summary = wait_for(client, job_id, lambda s: s["status"] == "done" and s["phase"] == "done" and s["error"] is None)
    assert client.get(f"/jobs/{job_id}/report").json()["fixes"][0]["verified"] is True


def test_shutdown_stops_a_task_hung_in_its_browser(work_dir: Path, hung: HungBrowser) -> None:
    store = JobStore(work_dir)
    runner = Runner(store, cancel_grace_s=FAST_GRACE_S)
    record = store.create("https://example.test/hang", AuditSettings())
    runner.submit(Task(record, "audit"))
    assert hung.started.wait(30)
    started = time.monotonic()
    runner.stop()
    assert time.monotonic() - started < 9  # well inside STOP_TIMEOUT_S: the watchdog killed the browser
    assert (record.status, record.error) == ("error", STOPPED_DURING)
    assert not runner._thread.is_alive()


# ------------------------------------------------------------------------------------ real Chromium
HANG_PAGE = """<!doctype html><title>hang</title>
<div style="width:400px;height:300px;background:#808080"></div>
<script>setTimeout(() => { for (;;) {} }, 300);</script>"""
STILL_PAGE = "<!doctype html><title>still</title><p>Nothing moves on this page.</p>"


@pytest.fixture
def real_client(tmp_path: Path) -> Iterator[TestClient]:
    """The real app and runner (no fakes), with a project holding a hanging page and a still one."""
    project = tmp_path / "project"
    project.mkdir()
    (project / "hang.html").write_text(HANG_PAGE, encoding="utf-8")
    (project / "still.html").write_text(STILL_PAGE, encoding="utf-8")
    before = browser_processes()
    app = create_app(tmp_path / "work", project, static_dir=tmp_path / "no-ui-build")
    with TestClient(app, base_url=BASE_URL) as client:
        yield client
    wait_until(lambda: not browser_processes() - before, timeout=30)  # no Chromium of ours is left


def wait_until_stuck(client: TestClient, job_id: str) -> None:
    """Until the capture has taken a frame and then made no progress for a whole second."""
    def frames() -> int:
        return client.get(f"/jobs/{job_id}").json()["progress"]["current"]

    wait_for(client, job_id, lambda s: s["phase"] == "capturing" and s["progress"]["current"] >= 1, timeout=60)
    previous = frames()
    for _ in range(60):
        time.sleep(1.0)
        current = frames()
        if current == previous:
            return
        previous = current
    raise AssertionError("the capture never got stuck")


@pytest.mark.browser
def test_delete_frees_the_runner_from_a_page_that_hangs_chromium(real_client: TestClient, tmp_path: Path) -> None:
    stuck = submit(real_client, url="hang.html", duration=2, attribute="none")
    wait_until_stuck(real_client, stuck)
    assert real_client.delete(f"/jobs/{stuck}").status_code == 204
    wait_until_gone(tmp_path / "work" / "jobs" / stuck, timeout=30)
    after = submit(real_client, url="still.html", duration=2, attribute="none")  # a PASS needs 2 s of frames
    summary = wait_for(real_client, after, lambda s: s["status"] in ("done", "error"), timeout=120)
    assert (summary["status"], summary["verdict"]) == ("done", "PASS"), summary


@pytest.mark.browser
def test_a_page_that_hangs_chromium_times_out(real_client: TestClient) -> None:
    real_client.app.state.server.runner.stall_limit_s = 5.0
    stuck = submit(real_client, url="hang.html", duration=2, attribute="none")
    summary = wait_for(real_client, stuck, lambda s: s["status"] in ("done", "error", "cancelled"), timeout=120)
    assert (summary["status"], summary["error"]) == ("error", stall_sentence(5.0)), summary
