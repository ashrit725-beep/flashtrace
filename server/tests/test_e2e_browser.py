"""End to end with the real runner: uvicorn on a free port, a real Chromium capture, a real report.

The page is capture/pages/css_keyframes.html: a 400x300 px box toggling black/white at 4 Hz, which the
engine must call FAIL.  Attribution runs in bbox mode when capture.attribute provides attribute(), and
is switched off otherwise (it is built by another milestone).
"""

from __future__ import annotations

import importlib
import importlib.util
import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
import uvicorn

from cli.report import DISCLAIMER, validate_report
from server.app import REPO_ROOT, create_app

PAGE = REPO_ROOT / "capture" / "pages" / "css_keyframes.html"
JOB_TIMEOUT_S = 180.0  # generous: the machine may be busy with a benchmark
POLL_INTERVAL_S = 1.0  # docs/API.md: poll at most once per second
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def attribution_available() -> bool:
    if importlib.util.find_spec("capture.attribute") is None:
        return False
    return callable(getattr(importlib.import_module("capture.attribute"), "attribute", None))


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.fixture
def live_server(tmp_path: Path) -> Iterator[str]:
    """The real app (real runner, real audit) served by uvicorn in a background thread."""
    app = create_app(tmp_path / "work", project_root=REPO_ROOT)
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, name="uvicorn-e2e", daemon=True)
    thread.start()
    deadline = time.monotonic() + 30
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    assert server.started, "uvicorn did not start"
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(60)


def poll_until_finished(client: httpx.Client, job_id: str) -> dict:
    deadline = time.monotonic() + JOB_TIMEOUT_S
    summary: dict = {}
    while time.monotonic() < deadline:
        summary = client.get(f"/jobs/{job_id}").json()
        if summary["status"] in ("error", "cancelled") or (summary["status"] == "done" and summary["phase"] == "done"):
            return summary
        time.sleep(POLL_INTERVAL_S)
    raise AssertionError(f"job did not finish within {JOB_TIMEOUT_S} s; last summary: {summary}")


@pytest.mark.browser
def test_audit_through_the_server_end_to_end(live_server: str) -> None:
    attribute = "bbox" if attribution_available() else "none"
    with httpx.Client(base_url=live_server, timeout=60) as client:
        created = client.post("/jobs", json={"url": str(PAGE), "duration": 2, "attribute": attribute})
        assert created.status_code == 201, created.text
        job_id = created.json()["id"]
        summary = poll_until_finished(client, job_id)
        assert summary["status"] == "done", summary
        assert summary["verdict"] == "FAIL" and summary["settings"]["attribute"] == attribute

        report = client.get(f"/jobs/{job_id}/report").json()
        validate_report(report)
        assert report["verdict"] == "FAIL" and report["capture"]["frameCount"] == 120
        if attribute == "bbox":
            assert report["culprits"], "bbox attribution found no culprit for the flashing box"

        frame = client.get(f"/jobs/{job_id}/frames/0.png")
        assert frame.status_code == 200 and frame.content.startswith(PNG_SIGNATURE)
        assert client.get(f"/jobs/{job_id}/frames/120.png").status_code == 404

        page = client.get(f"/jobs/{job_id}/report.html")
        assert page.status_code == 200 and page.headers["content-type"].startswith("text/html")
        assert DISCLAIMER in page.text
