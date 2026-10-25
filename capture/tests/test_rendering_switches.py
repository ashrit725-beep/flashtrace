"""ADR-025: every capture runs Chromium with switches that keep machine load out of the pixels."""
from __future__ import annotations

import multiprocessing as mp
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pytest

from capture.capturer import RENDERING_SWITCHES, launch_chromium

PAGES = Path(__file__).resolve().parents[1] / "pages"


class _FakeBrowserType:
    """Records the arguments launch() receives instead of starting a browser."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def launch(self, **kwargs: object) -> str:
        self.calls.append(kwargs)
        return "browser"


def test_launch_chromium_always_passes_the_rendering_switches() -> None:
    """ADR-025: the switches come first, strategy-specific ones are appended."""
    fake = _FakeBrowserType()
    launch_chromium(fake, ["--example-strategy-switch"])
    assert fake.calls == [{"headless": True, "args": [*RENDERING_SWITCHES, "--example-strategy-switch"]}]
    assert "--disable-threaded-animation" in RENDERING_SWITCHES  # the measured root cause


def _capture_hashes(page: str) -> list[str]:
    """Runs in a separate process so the captures compete for the CPU like benchmark workers do."""
    import tempfile

    from capture.capturer import capture

    with tempfile.TemporaryDirectory() as out:
        return capture(page, duration_s=10, viewport=(1024, 768), fps=60, out_dir=out).meta.frame_hashes


@pytest.mark.browser
def test_concurrent_captures_of_a_scrolling_marquee_are_identical() -> None:
    """SPEC 6.3 under load: four captures at the same time give the same 600 frame hashes.

    Before ADR-025 this page (modelled on the benchmark's marquee distractor) differed in a 7 px band of
    newly revealed text in most concurrent runs.
    """
    page = str(PAGES / "marquee.html")
    with ProcessPoolExecutor(4, mp_context=mp.get_context("spawn")) as pool:
        runs = list(pool.map(_capture_hashes, [page] * 4))
    assert all(len(run) == 600 for run in runs)
    differing = [sum(a != b for a, b in zip(runs[0], other)) for other in runs[1:]]
    assert differing == [0, 0, 0], f"frames differing from run 0: {differing}"
