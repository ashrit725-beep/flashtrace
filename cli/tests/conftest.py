"""pytest configuration for the cli tests.

Registers the ``browser`` mark (``-m "not browser"`` skips every test that launches Chromium) and builds
three small synthetic captures once per session: a 4 Hz full-frame square wave (FAIL), a static grey page
held for 2 s (PASS) and the same page held for only 1 s (too short for a PASS).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cli.tests.helpers import FPS, AnalysedJob, analysed_job, square_wave, static_grey


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "browser: needs headless Chromium (deselect with -m 'not browser')")


@pytest.fixture(scope="session")
def work_base(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("flashtrace-work")


@pytest.fixture(scope="session")
def fail_job(work_base: Path) -> AnalysedJob:
    """2 s of a 4 Hz black/white square wave: engine FAIL at 4 flashes per second."""
    return analysed_job(work_base, square_wave(4), 2 * FPS)


@pytest.fixture(scope="session")
def pass_job(work_base: Path) -> AnalysedJob:
    """2 s of a static grey page: engine PASS, long enough for a PASS verdict."""
    return analysed_job(work_base, static_grey(), 2 * FPS)


@pytest.fixture(scope="session")
def short_pass_job(work_base: Path) -> AnalysedJob:
    """1 s of a static grey page: engine PASS but fewer than 2*fps frames."""
    return analysed_job(work_base, static_grey(), FPS)
