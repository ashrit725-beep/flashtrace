"""Strategy A seek sanity: the screenshots show the state the page was seeked to."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest

from capture import capture

FPS = 60
# css_keyframes.html: #box is 400x300 at (100,100), so its centre is (300, 250) in (x, y).
BOX_CENTRE_XY = (300, 250)
BACKGROUND_XY = (50, 50)


def box_state(frame: np.ndarray) -> str:
    """'black', 'white' or 'other' for the centre pixel of #box."""
    pixel = frame[BOX_CENTRE_XY[1], BOX_CENTRE_XY[0]]
    if pixel.max() < 10:
        return "black"
    if pixel.min() > 245:
        return "white"
    return f"other{pixel.tolist()}"


def expected_state(t_seconds: float) -> str:
    """The page's contract: 125 ms black, 125 ms white, repeating (4 Hz)."""
    return "black" if math.floor(t_seconds * 8) % 2 == 0 else "white"


@pytest.mark.browser
def test_css_keyframes_seek_matches_frame_time(pages_dir: Path, tmp_path: Path, browser) -> None:
    result = capture(
        str(pages_dir / "css_keyframes.html"),
        duration_s=1,
        fps=FPS,
        out_dir=tmp_path,
        browser=browser,
    )
    meta = result.meta
    print(f"[wall] css_keyframes 1 s @ {FPS} fps: {meta.wall_time_s:.2f} s ({meta.strategy})")

    assert meta.strategy == "waapi_step"
    assert meta.deterministic is True
    assert meta.uncontrollable == []
    assert meta.frame_count == 60
    assert len(result.frames) == 60
    assert len(meta.frame_hashes) == 60
    assert all(len(h) == 64 for h in meta.frame_hashes)

    for index in (0, 10, 20, 30, 40, 50, 59):
        observed = box_state(result.frames[index])
        t = index / FPS
        # A frame that sits on a 125 ms boundary may legitimately show either neighbour's state
        # (one-frame tolerance), but it must never be anything other than pure black or white.
        acceptable = {expected_state(t), expected_state((index - 1) / FPS), expected_state((index + 1) / FPS)}
        assert observed in acceptable, f"frame {index} (t={t:.4f}s): {observed} not in {acceptable}"

    for index, frame in enumerate(result.frames):
        assert frame.shape == (768, 1024, 3)
        background = frame[BACKGROUND_XY[1], BACKGROUND_XY[0]]
        assert background.tolist() == [255, 255, 255], f"background not white on frame {index}"
        assert box_state(frame) in ("black", "white"), f"frame {index} shows a mid-transition colour"


@pytest.mark.browser
def test_every_frame_follows_the_square_wave(pages_dir: Path, tmp_path: Path, browser) -> None:
    """Stronger version of the spot check: the full 60-frame state sequence has exactly the expected
    transitions (allowing each transition to land one frame early or late)."""
    result = capture(str(pages_dir / "css_keyframes.html"), duration_s=1, fps=FPS, out_dir=tmp_path, browser=browser)
    observed = [box_state(f) for f in result.frames]
    expected = [expected_state(i / FPS) for i in range(60)]
    mismatches = [i for i, (o, e) in enumerate(zip(observed, expected, strict=True)) if o != e]
    # Any mismatch must be adjacent to an expected transition (boundary rounding), never elsewhere.
    transitions = {i for i in range(1, 60) if expected[i] != expected[i - 1]}
    for i in mismatches:
        assert i in transitions or (i + 1) in transitions, f"frame {i} is wrong away from any boundary"
    assert len(mismatches) <= len(transitions)
    assert observed[0] == "black"
