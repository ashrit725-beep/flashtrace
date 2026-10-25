"""Shared fixtures for the server tests.

* ``project``  - a temporary "project directory" holding ``page.html`` (the only local file allowed);
* ``pipeline`` - the FakePipeline installed in place of the real audit functions (no browser);
* ``make_client`` - builds a TestClient for ``create_app`` with chosen options; clients are closed (and
  the runner stopped) at the end of the test;
* ``client``   - one default client.

The TestClient talks to the app as http://127.0.0.1:8001, because the app refuses other Host headers.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from cli import audit as cli_audit
from server.app import create_app
from server.tests.fakes import FakePipeline

BASE_URL = "http://127.0.0.1:8001"


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "browser: needs headless Chromium (deselect with -m 'not browser')")


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    (root / "page.html").write_text("<!doctype html><title>page</title>", encoding="utf-8")
    return root


@pytest.fixture
def work_dir(tmp_path: Path) -> Path:
    return tmp_path / "work"


@pytest.fixture
def pipeline(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakePipeline]:
    fake = FakePipeline()
    monkeypatch.setattr(cli_audit, "run_audit", fake.run_audit)
    monkeypatch.setattr(cli_audit, "reattribute", fake.reattribute, raising=False)
    monkeypatch.setattr(cli_audit, "verify_fix_in_job", fake.verify_fix_in_job, raising=False)
    yield fake
    fake.release_all()


@pytest.fixture
def make_client(project: Path, work_dir: Path, pipeline: FakePipeline, tmp_path: Path) -> Iterator[Callable[..., TestClient]]:
    opened: list[TestClient] = []

    def build(**options: Any) -> TestClient:
        options.setdefault("static_dir", tmp_path / "no-ui-build")
        app = create_app(work_dir, project, **options)
        client = TestClient(app, base_url=BASE_URL)
        client.__enter__()  # runs the app's lifespan (its exit stops the runner thread)
        opened.append(client)
        return client

    yield build
    pipeline.release_all()  # let a held fake finish so the runner can stop at once
    for client in reversed(opened):
        client.__exit__(None, None, None)


@pytest.fixture
def client(make_client: Callable[..., TestClient]) -> TestClient:
    return make_client()
