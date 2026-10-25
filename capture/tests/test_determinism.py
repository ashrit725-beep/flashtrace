"""SPEC 6.3 determinism: the same page captured twice under strategy A gives identical frame hashes,
and the three synthetic pages (CSS keyframes, WAAPI, rAF) draw the same thing."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import numpy as np
import pytest
from playwright.sync_api import Browser

from capture import capture

FPS = 60
DURATION_S = 2
PAGES = ("css_keyframes.html", "waapi.html", "raf.html")
BOX_CENTRE_XY = (300, 250)


def box_states(frames: Iterable[np.ndarray]) -> list[str]:
    states = []
    for frame in frames:
        pixel = frame[BOX_CENTRE_XY[1], BOX_CENTRE_XY[0]]
        states.append("black" if pixel.max() < 10 else "white" if pixel.min() > 245 else f"other{pixel.tolist()}")
    return states


def transition_frames(states: list[str]) -> list[int]:
    """Indices at which the state changes."""
    return [i for i in range(1, len(states)) if states[i] != states[i - 1]]


def assert_same_square_wave(a: list[str], b: list[str], tolerance: int = 1) -> None:
    """Same initial state and the same transitions, each allowed to differ by ``tolerance`` frames.

    Why a tolerance: CSS/WAAPI animations are seeked to the exact frame time, but a rAF loop can only
    update when the fake requestAnimationFrame fires (on multiples of 16 ticks), so its transitions may
    land one frame later. That is a property of sampled rAF, not a capture error.
    """
    assert a[0] == b[0]
    ta, tb = transition_frames(a), transition_frames(b)
    assert len(ta) == len(tb), f"different transition counts: {ta} vs {tb}"
    for x, y in zip(ta, tb, strict=True):
        assert abs(x - y) <= tolerance, f"transition at frame {x} vs {y}"


@pytest.fixture(scope="module")
def captures(pages_dir: Path, tmp_path_factory: pytest.TempPathFactory, browser: Browser) -> dict[str, list]:
    """Each page captured twice (2 s at 60 fps); shared by the tests below."""
    results: dict[str, list] = {}
    for page in PAGES:
        pair = []
        for run in (1, 2):
            out = tmp_path_factory.mktemp(f"{Path(page).stem}-{run}")
            result = capture(str(pages_dir / page), duration_s=DURATION_S, fps=FPS, out_dir=out, browser=browser)
            print(f"[wall] {page} run {run} {DURATION_S} s @ {FPS} fps: {result.meta.wall_time_s:.2f} s")
            pair.append(result)
        results[page] = pair
    return results


@pytest.mark.browser
@pytest.mark.parametrize("page", PAGES)
def test_two_captures_are_identical(captures: dict[str, list], page: str) -> None:
    first, second = captures[page]
    assert first.meta.strategy == second.meta.strategy == "waapi_step"
    assert first.meta.deterministic and second.meta.deterministic, (first.meta.notes, second.meta.notes)
    assert first.meta.frame_count == second.meta.frame_count == DURATION_S * FPS
    assert first.meta.frame_hashes == second.meta.frame_hashes
    assert first.meta.hash == second.meta.hash
    assert first.meta.fallback_reason is None


@pytest.mark.browser
def test_pages_draw_the_same_square_wave(captures: dict[str, list]) -> None:
    states = {page: box_states(captures[page][0].frames) for page in PAGES}
    for page, sequence in states.items():
        assert all(s in ("black", "white") for s in sequence), f"{page} shows a non-pure colour"
        # 4 Hz over 2 s: 16 half-periods, so 15 transitions inside the capture.
        assert len(transition_frames(sequence)) == 15, f"{page}: {transition_frames(sequence)}"
    # CSS and WAAPI are both seeked exactly: they must match frame for frame.
    assert states["css_keyframes.html"] == states["waapi.html"]
    assert_same_square_wave(states["css_keyframes.html"], states["raf.html"], tolerance=1)


@pytest.mark.browser
def test_static_page_never_changes(pages_dir: Path, tmp_path: Path, browser: Browser) -> None:
    result = capture(str(pages_dir / "static.html"), duration_s=0.5, fps=FPS, out_dir=tmp_path, browser=browser)
    print(f"[wall] static.html 0.5 s @ {FPS} fps: {result.meta.wall_time_s:.2f} s")
    assert result.meta.frame_count == 30
    assert len(set(result.meta.frame_hashes)) == 1
    assert result.meta.deterministic is True
    assert any(note.startswith("0 animation(s) found at frame 0") for note in result.meta.notes), result.meta.notes
    assert np.array_equal(result.frames[0], result.frames[-1])
