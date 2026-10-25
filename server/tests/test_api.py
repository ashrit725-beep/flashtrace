"""Every endpoint and error code of docs/API.md, against the fake pipeline (no browser)."""

from __future__ import annotations

import importlib.metadata
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cli.jobs import META_JSON_NAME, read_meta
from cli.report import DISCLAIMER, validate_report
from engine import ENGINE_VERSION
from server.tests.fakes import FAKE_REASON, FRAME_COUNT, FakePipeline, submit, wait_for, wait_until_finished

SUMMARY_KEYS = {"id", "url", "createdAt", "status", "phase", "progress", "settings", "verdict", "reason", "error"}
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def job_dir(work_dir: Path, job_id: str) -> Path:
    return work_dir / "jobs" / job_id


def finished_job(client: TestClient) -> str:
    job_id = submit(client)
    summary = wait_until_finished(client, job_id)
    assert summary["status"] == "done", summary
    return job_id


def held_running_job(client: TestClient, pipeline: FakePipeline) -> str:
    """A job whose (fake) capture is in progress and stays there until the test releases it."""
    pipeline.hold_audit.clear()
    job_id = submit(client)
    assert pipeline.audit_waiting.wait(30), "the fake capture never started"
    wait_for(client, job_id, lambda s: s["status"] == "running" and s["phase"] == "capturing")
    return job_id


# ------------------------------------------------------------------------------------ health
def test_health(client: TestClient) -> None:
    assert client.get("/health").json() == {"ok": True, "version": importlib.metadata.version("flashtrace"),
                                            "engineVersion": ENGINE_VERSION}


# ------------------------------------------------------------------------------------ POST /jobs
def test_post_jobs_returns_201_with_an_id_and_creates_a_queued_job_directory(client: TestClient, work_dir: Path,
                                                                            pipeline: FakePipeline) -> None:
    pipeline.hold_audit.clear()
    response = client.post("/jobs", json={"url": "https://example.test/"})
    assert response.status_code == 201
    assert set(response.json()) == {"id"}
    meta = read_meta(job_dir(work_dir, response.json()["id"]))
    assert meta["source"] == "server" and meta["url"] == "https://example.test/"


def test_defaults_and_summary_shape(client: TestClient) -> None:
    summary = client.get(f"/jobs/{submit(client)}").json()
    assert set(summary) == SUMMARY_KEYS
    # Exactly the POST /jobs fields, so the UI can resubmit them ("Retry") without an unknown-field 422.
    assert summary["settings"] == {"duration": 10.0, "viewport": {"width": 1024, "height": 768}, "attribute": "bbox"}
    assert set(summary["progress"]) == {"current", "total", "label"}


def test_summary_settings_can_be_resubmitted_unchanged(client: TestClient) -> None:
    first = client.get(f"/jobs/{submit(client, duration=2.5, viewport={'width': 200, 'height': 3840}, attribute='none')}").json()
    again = client.post("/jobs", json={"url": first["url"], **first["settings"]})
    assert again.status_code == 201, again.text


@pytest.mark.parametrize("body", [
    {},                                                        # url missing
    {"url": 5},                                                # url not a string
    {"url": "https://example.test/", "colour": "red"},        # unknown field
    {"url": "https://example.test/", "duration": 0.5},        # below 1 s
    {"url": "https://example.test/", "duration": 61},         # above 60 s
    {"url": "https://example.test/", "duration": "10"},       # a string, not a number
    {"url": "https://example.test/", "duration": True},
    {"url": "https://example.test/", "viewport": {"width": 199, "height": 768}},
    {"url": "https://example.test/", "viewport": {"width": 1024, "height": 3841}},
    {"url": "https://example.test/", "viewport": {"width": 1024.0, "height": 768}},
    {"url": "https://example.test/", "viewport": {"width": 1024}},
    {"url": "https://example.test/", "viewport": {"width": 1024, "height": 768, "dpr": 2}},
    {"url": "https://example.test/", "attribute": "everything"},
    {"url": "javascript:alert(1)"},
    {"url": "page-that-does-not-exist.html"},
    {"url": "file:///x/%00"},                                  # decodes to NUL (was a 500)
    {"url": "~nosuchuser123/page.html"},                       # unknown home folder (was a 500)
])
def test_post_jobs_invalid_input_is_422_with_a_one_sentence_detail(client: TestClient, body: dict) -> None:
    response = client.post("/jobs", json=body)
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert isinstance(detail, str) and detail.endswith(".") and "\n" not in detail


def test_post_jobs_body_that_is_not_json_is_422(client: TestClient) -> None:
    response = client.post("/jobs", content=b"{nope", headers={"content-type": "application/json"})
    assert response.status_code == 422
    assert response.json()["detail"] == "request body: the body is not valid JSON."


def test_local_file_inside_project_is_accepted_and_stored_as_file_url(client: TestClient, project: Path) -> None:
    summary = client.get(f"/jobs/{submit(client, url='page.html')}").json()
    assert summary["url"] == (project / "page.html").resolve().as_uri()


def test_local_file_outside_project_needs_allow_local_files(make_client, tmp_path: Path) -> None:
    outside = tmp_path / "outside.html"
    outside.write_text("<!doctype html>", encoding="utf-8")
    refused = make_client().post("/jobs", json={"url": str(outside)})
    assert refused.status_code == 422 and "outside the project directory" in refused.json()["detail"]
    accepted = make_client(allow_local_files=True).post("/jobs", json={"url": str(outside)})
    assert accepted.status_code == 201


# ------------------------------------------------------------------------------------ GET /jobs, /jobs/{id}
def test_list_is_newest_first(client: TestClient) -> None:
    ids = [submit(client, url=f"https://example.test/{n}") for n in range(3)]
    for job_id in ids:
        wait_until_finished(client, job_id)
    listed = [summary["id"] for summary in client.get("/jobs").json()]
    assert listed == sorted(ids, reverse=True)


def test_get_job_unknown_is_404_and_malformed_is_422(client: TestClient) -> None:
    assert client.get("/jobs/20200101-000000-abcdef").status_code == 404
    response = client.get("/jobs/not-a-job")
    assert response.status_code == 422 and "job id" in response.json()["detail"]


def test_finished_job_summary(client: TestClient) -> None:
    summary = client.get(f"/jobs/{finished_job(client)}").json()
    assert summary["status"] == "done" and summary["phase"] == "done"
    assert summary["verdict"] == "FAIL" and summary["reason"] == FAKE_REASON and summary["error"] is None
    assert summary["createdAt"].endswith("Z")


def test_progress_reports_frames_while_capturing(client: TestClient, pipeline: FakePipeline) -> None:
    summary = client.get(f"/jobs/{held_running_job(client, pipeline)}").json()
    assert summary["progress"] == {"current": FRAME_COUNT - 1, "total": FRAME_COUNT,
                                   "label": f"frames {FRAME_COUNT - 1}/{FRAME_COUNT}"}
    assert summary["verdict"] is None


def test_attribution_phase_of_a_new_audit(client: TestClient, pipeline: FakePipeline) -> None:
    pipeline.hold_attribution.clear()
    job_id = submit(client, attribute="bbox")
    summary = wait_for(client, job_id, lambda s: s["phase"] == "attributing")
    assert summary["status"] == "running"
    assert summary["progress"] == {"current": 0, "total": 0, "label": "attributing (bbox)"}  # no counts: no bar
    pipeline.hold_attribution.set()
    assert wait_until_finished(client, job_id)["status"] == "done"


# ------------------------------------------------------------------------------------ report, html, frames
def test_report_html_and_frames_of_a_finished_job(client: TestClient) -> None:
    job_id = finished_job(client)
    report = client.get(f"/jobs/{job_id}/report")
    assert report.status_code == 200 and report.headers["content-type"].startswith("application/json")
    validate_report(report.json())
    page = client.get(f"/jobs/{job_id}/report.html")
    assert page.status_code == 200 and page.headers["content-type"].startswith("text/html")
    assert DISCLAIMER in page.text
    for index in (0, FRAME_COUNT - 1):
        frame = client.get(f"/jobs/{job_id}/frames/{index}.png")
        assert frame.status_code == 200 and frame.headers["content-type"] == "image/png"
        assert frame.content.startswith(PNG_SIGNATURE)


@pytest.mark.parametrize("index", ["-1", str(FRAME_COUNT), "99999999"])
def test_frame_outside_the_capture_is_404(client: TestClient, index: str) -> None:
    assert client.get(f"/jobs/{finished_job(client)}/frames/{index}.png").status_code == 404


@pytest.mark.parametrize("index", ["abc", "1.5", "0x1", "..", "%2e%2e"])
def test_frame_index_that_is_not_a_whole_number_is_422(client: TestClient, index: str) -> None:
    assert client.get(f"/jobs/{finished_job(client)}/frames/{index}.png").status_code == 422


def test_frame_traversal_attempts_never_reach_a_file(client: TestClient) -> None:
    job_id = finished_job(client)
    for path in (f"/jobs/{job_id}/frames/../meta.json", f"/jobs/{job_id}/frames/%2e%2e/{META_JSON_NAME}",
                 f"/jobs/{job_id}/frames/..%2F..%2F{META_JSON_NAME}", "/jobs/..%2F..%2Fetc/report"):
        response = client.get(path)
        assert response.status_code in (404, 422)
        assert "jobId" not in response.text


def test_report_html_and_frames_are_409_while_running(client: TestClient, pipeline: FakePipeline) -> None:
    job_id = held_running_job(client, pipeline)
    for path in ("report", "report.html", "frames/0.png"):
        response = client.get(f"/jobs/{job_id}/{path}")
        assert response.status_code == 409, path
        assert isinstance(response.json()["detail"], str)


def test_job_that_errored_before_a_report_is_404_for_report_and_400_for_follow_ups(client: TestClient,
                                                                                  pipeline: FakePipeline) -> None:
    pipeline.fail_with = RuntimeError("the page did not load\nsecond line")
    job_id = submit(client)
    summary = wait_until_finished(client, job_id)
    assert summary["status"] == "error" and summary["phase"] == "error"
    assert summary["error"] == "The audit failed (RuntimeError: the page did not load)."
    for path in ("report", "report.html", "frames/0.png"):
        assert client.get(f"/jobs/{job_id}/{path}").status_code == 404, path
    assert client.post(f"/jobs/{job_id}/ablate").status_code == 400
    assert client.post(f"/jobs/{job_id}/fixes/0/verify").status_code == 400


def test_unknown_job_is_404_on_every_job_endpoint(client: TestClient) -> None:
    missing = "20200101-000000-abcdef"
    for method, path in (("get", ""), ("get", "/report"), ("get", "/report.html"), ("get", "/frames/0.png"),
                         ("post", "/ablate"), ("post", "/fixes/0/verify"), ("delete", "")):
        assert getattr(client, method)(f"/jobs/{missing}{path}").status_code == 404, (method, path)


# ------------------------------------------------------------------------------------ ablate
def test_ablate_returns_202_summary_then_replaces_culprits(client: TestClient, pipeline: FakePipeline) -> None:
    job_id = finished_job(client)
    pipeline.hold_follow_up.clear()
    response = client.post(f"/jobs/{job_id}/ablate")
    assert response.status_code == 202 and set(response.json()) == SUMMARY_KEYS
    running = wait_for(client, job_id, lambda s: s["phase"] == "ablating" and s["progress"]["total"] == 2)
    assert running["status"] == "running" and running["settings"]["attribute"] == "bbox"
    assert running["progress"] == {"current": 1, "total": 2, "label": "ablation 1: suppressed ['#box']"}
    assert client.get(f"/jobs/{job_id}/report").status_code == 409  # "409 while the job is not done"
    assert client.post(f"/jobs/{job_id}/ablate").status_code == 409  # busy
    # The frames do not change during a follow-up task, so the report screen's frame viewer keeps working.
    assert client.get(f"/jobs/{job_id}/frames/0.png").status_code == 200
    pipeline.hold_follow_up.set()
    done = wait_until_finished(client, job_id)
    assert done["status"] == "done" and done["settings"]["attribute"] == "ablate"  # as the report now says
    culprit = client.get(f"/jobs/{job_id}/report").json()["culprits"][0]
    assert culprit["ablationDelta"] == 8.0 and culprit["inMinimalSet"] is True


def test_ablate_on_a_running_job_is_409(client: TestClient, pipeline: FakePipeline) -> None:
    assert client.post(f"/jobs/{held_running_job(client, pipeline)}/ablate").status_code == 409


def test_failed_ablation_leaves_the_audit_done_with_the_error_and_its_report(client: TestClient, monkeypatch) -> None:
    """A failed follow-up does not take the audit away: status and phase stay "done" (so the UI routes to
    the report), and ``error`` says why the re-run did not finish."""
    from cli import audit as cli_audit

    job_id = finished_job(client)

    def broken(job_dir: Path, mode: str = "ablate") -> dict:
        raise ValueError("no candidates could be suppressed")

    monkeypatch.setattr(cli_audit, "reattribute", broken)
    client.post(f"/jobs/{job_id}/ablate")
    summary = wait_until_finished(client, job_id)
    assert (summary["status"], summary["phase"]) == ("done", "done")
    assert summary["error"].startswith("Ablation failed (ValueError")
    assert summary["verdict"] == "FAIL" and summary["reason"] == FAKE_REASON
    assert summary["settings"]["attribute"] == "bbox"  # the report was not replaced
    assert client.get(f"/jobs/{job_id}/report").status_code == 200
    assert client.post(f"/jobs/{job_id}/ablate").status_code == 202  # and it can be tried again


# ------------------------------------------------------------------------------------ verify
def test_verify_returns_202_then_sets_after_and_verified(client: TestClient, pipeline: FakePipeline) -> None:
    job_id = finished_job(client)
    pipeline.hold_follow_up.clear()
    response = client.post(f"/jobs/{job_id}/fixes/0/verify")
    assert response.status_code == 202 and set(response.json()) == SUMMARY_KEYS
    verifying = wait_for(client, job_id, lambda s: s["phase"] == "verifying" and s["progress"]["current"] == 1)
    assert verifying["status"] == "running" and verifying["progress"]["label"] == f"frames 1/{FRAME_COUNT}"
    pipeline.hold_follow_up.set()
    wait_until_finished(client, job_id)
    fix = client.get(f"/jobs/{job_id}/report").json()["fixes"][0]
    assert fix["verified"] is True and fix["after"]["verdict"] == "PASS"
    assert pipeline.verified[-1][1] == 0


def test_verify_fix_number_checks(client: TestClient) -> None:
    job_id = finished_job(client)
    missing = client.post(f"/jobs/{job_id}/fixes/1/verify")
    assert missing.status_code == 404 and "no fix 1" in missing.json()["detail"]
    assert client.post(f"/jobs/{job_id}/fixes/-1/verify").status_code == 422
    assert client.post(f"/jobs/{job_id}/fixes/first/verify").status_code == 422


def test_verify_on_a_running_job_is_409(client: TestClient, pipeline: FakePipeline) -> None:
    assert client.post(f"/jobs/{held_running_job(client, pipeline)}/fixes/0/verify").status_code == 409


# ------------------------------------------------------------------------------------ DELETE
def test_delete_finished_job_removes_it_and_its_directory(client: TestClient, work_dir: Path) -> None:
    job_id = finished_job(client)
    assert job_dir(work_dir, job_id).is_dir()
    response = client.delete(f"/jobs/{job_id}")
    assert response.status_code == 204 and response.content == b""
    assert not job_dir(work_dir, job_id).exists()
    assert client.get(f"/jobs/{job_id}").status_code == 404
    assert job_id not in [summary["id"] for summary in client.get("/jobs").json()]


def test_delete_malformed_id_is_422(client: TestClient) -> None:
    assert client.delete("/jobs/..").status_code in (404, 405, 422)
    assert client.delete("/jobs/not-an-id").status_code == 422
