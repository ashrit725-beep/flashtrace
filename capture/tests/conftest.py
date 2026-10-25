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


@pytest.fixture(scope="session")
def browser() -> Iterator["Browser"]:  # noqa: F821 - imported lazily so non-browser runs need no Playwright
    """One headless Chromium for the whole session (launch costs ~0.5 s; contexts are per capture)."""
    from playwright.sync_api import sync_playwright

    from capture.capturer import launch_chromium

    with sync_playwright() as playwright:
        instance = launch_chromium(playwright.chromium)
        try:
            yield instance
        finally:
            instance.close()
