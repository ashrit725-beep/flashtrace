"""ADR-029: where in each frame slot FlashTrace samples, and animation-frame callbacks once per frame."""
from __future__ import annotations

from fractions import Fraction
from pathlib import Path
from typing import Any

import pytest

from capture.strategies.base import SAMPLE_PHASE, CaptureSettings

PAGES = Path(__file__).resolve().parents[1] / "pages"


def test_sample_times_sit_a_quarter_frame_into_each_slot() -> None:
    """SPEC 5.1: frame i shows the page at (i + 1/4) / fps."""
    settings = CaptureSettings(url="about:blank", duration_s=1, viewport=(1024, 768), fps=60)
    assert SAMPLE_PHASE == 0.25
    assert settings.frame_time_ms(0) == pytest.approx(1000 / 240)
    assert settings.frame_time_ms(59) == pytest.approx(59.25 * 1000 / 60)


def test_sample_times_never_fall_on_a_whole_or_third_millisecond_at_60_fps() -> None:
    """ADR-029: so a CSS/WAAPI state switch timed in whole or third milliseconds is never sampled exactly."""
    for i in range(6000):
        t = Fraction(4 * i + 1, 4) * Fraction(1000, 60)  # exact (i + 1/4) * 1000 / 60
        assert (t * 3).denominator != 1, f"frame {i} samples at {t} ms"


def _rate_and_gaps(page: str, seconds: float, browser: Any) -> tuple[str, int, set[int]]:
    import tempfile

    from capture.capturer import capture
    from engine import analyze

    with tempfile.TemporaryDirectory() as out:
        captured = capture(page, duration_s=seconds, viewport=(1024, 768), fps=60, out_dir=out, browser=browser)
        result = analyze(captured.frames, fps=60)
    events = result.qualifying_general
    return result.verdict, result.general_max_rate, {b - a for a, b in zip(events, events[1:])}


@pytest.mark.browser
def test_a_5hz_strobe_measures_exactly_5_per_second(browser: Any) -> None:
    """ADR-029: switches every 6 frames give flashes exactly 12 frames apart (it once measured 6 per second)."""
    verdict, rate, gaps = _rate_and_gaps(str(PAGES / "strobe_5hz.html"), 3, browser)
    assert (verdict, rate, gaps) == ("FAIL", 5, {12})


@pytest.mark.browser
def test_an_exact_3hz_raf_page_passes_at_3_per_second(browser: Any) -> None:
    """ADR-029 + WCAG "no more than three": rAF runs once per captured frame, so gaps are exactly 20 frames."""
    verdict, rate, gaps = _rate_and_gaps(str(PAGES / "raf_3hz.html"), 3, browser)
    assert (verdict, rate, gaps) == ("PASS", 3, {20})
