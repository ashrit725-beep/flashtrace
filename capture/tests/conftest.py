"""pytest configuration for the capture tests.

Registers the ``browser`` mark (``-m "not browser"`` skips every test that launches Chromium) and
shares one Chromium process across the browser tests; each capture still uses a fresh context.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

PAGES_DIR = Path(__file__).resolve().parent.parent / "pages"


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "browser: needs headless Chromium (deselect with -m 'not browser')")


@pytest.fixture(scope="session")
def pages_dir() -> Path:
    """Directory of the synthetic test pages."""
    return PAGES_DIR


@pytest.fixture(scope="package")
def browser() -> Iterator["Browser"]:  # noqa: F821 - imported lazily so non-browser runs need no Playwright
    """One headless Chromium for the capture tests; each capture still gets a fresh context.

    Package scope, not session: it is closed when the last test in capture/tests has run. While its
    Playwright is open, the sync API refuses to start another one in this thread, so a session-wide
    instance would break every later test that opens its own (cli, tests/test_ui_self_audit.py).
    Tests in this package that run the pipeline in-process must pass this browser along.
    """
    from playwright.sync_api import sync_playwright

    from capture.capturer import launch_chromium

    with sync_playwright() as playwright:
        instance = launch_chromium(playwright.chromium)
        try:
            yield instance
        finally:
            instance.close()
