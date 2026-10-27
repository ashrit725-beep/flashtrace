"""Strategies B (``virtual_time``) and C (``realtime``) and the A -> B -> C fall-through (SPEC 6.2, 6.3).

Pure tests cover the screencast resampling rule and strategy A's network wait. Browser tests (``-m browser``) capture the synthetic
pages and *measure* what each strategy does on this machine; a B test that cannot run because the
platform lacks BeginFrameControl is skipped with the measured reason, never passed silently.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from playwright.sync_api import Browser
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from capture import CaptureResult, capture
from capture.strategies import network_wait
from capture.strategies import virtual_time as vt
from capture.strategies.realtime import lattice_phase, resample_indices, sample_origin

FPS = 60
# Every synthetic page puts its flashing element's centre at (300, 250) in (x, y).
CENTRE_XY = (300, 250)
CONTROLLABLE_PAGES = ("css_keyframes.html", "waapi.html", "raf.html")


def centre_states(frames: Iterable[np.ndarray]) -> list[str]:
    """'black' / 'white' / 'other[...]' for the probe pixel of every frame."""
    states = []
    for frame in frames:
        pixel = frame[CENTRE_XY[1], CENTRE_XY[0]]
        states.append("black" if pixel.max() < 10 else "white" if pixel.min() > 245 else f"other{pixel.tolist()}")
    return states


def note_matching(notes: list[str], pattern: str) -> str:
    """The first note matching ``pattern`` (regex); fails the test with all notes when absent."""
    for note in notes:
        if re.search(pattern, note):
            return note
    raise AssertionError(f"no note matches {pattern!r} in {notes}")


# ------------------------------------------------------------------------------- pure: resampling
def test_resample_holds_latest_frame_at_or_before_each_sample() -> None:
    # Source frames at 0, 50 and 100 ms; 60 fps samples at 0, 16.7, 33.3, 50, 66.7, 83.3, 100, 116.7 ms.
    assert resample_indices([0.0, 0.05, 0.1], origin=0.0, fps=60, count=8) == [0, 0, 0, 1, 1, 1, 2, 2]


def test_resample_repeats_first_frame_before_first_timestamp() -> None:
    # Samples at 0.98, 0.9967, 1.0133, 1.03 s; the first source frame is at 1.0 s.
    assert resample_indices([1.0, 1.03], origin=0.98, fps=60, count=4) == [0, 0, 0, 1]


def test_resample_static_source_is_held_for_every_sample() -> None:
    assert resample_indices([5.0], origin=5.0, fps=60, count=30) == [0] * 30


def test_resample_sample_exactly_on_a_timestamp_takes_that_frame() -> None:
    # "at or before": a sample that lands exactly on a timestamp uses that frame, not the previous one.
    assert resample_indices([0.0, 0.5], origin=0.0, fps=2, count=3) == [0, 1, 1]


def test_resample_rejects_an_empty_recording() -> None:
    with pytest.raises(ValueError):
        resample_indices([], origin=0.0, fps=60, count=1)


def test_lattice_phase_is_a_circular_mean() -> None:
    """0.98 and 0.02 of a tick average to 0, not to 0.5; frames spread evenly agree on nothing."""
    tick = 1 / 60
    phase, agreement = lattice_phase([0.0, 1.98 * tick, 3.02 * tick, 5.98 * tick, 7.02 * tick])
    assert min(phase, 1 - phase) < 0.01 and agreement > 0.99
    _phase, spread = lattice_phase([0.0, 1.25 * tick, 2.5 * tick, 3.75 * tick])
    assert spread < 0.01


def jittered_square_wave(seed: int, hz: float, seconds: float, jitter_s: float) -> tuple[list[float], list[str]]:
    """Screencast-like source frames: a full-screen square wave of ``hz`` whose changes the compositor
    shows at its next 60 Hz tick, each timestamp off by up to ``jitter_s``; frames only on change."""
    import random

    rng = random.Random(seed)
    stamps, states = [0.0], ["black"]
    for k in range(1, int(seconds * hz * 2)):
        tick = math.ceil(k / (2 * hz) * 60 - 1e-9)  # first compositor tick at or after the change
        stamps.append(tick / 60 + rng.uniform(-jitter_s, jitter_s))
        states.append("white" if k % 2 else "black")
    return stamps, states


@pytest.mark.parametrize("seed", range(5))
def test_samples_half_a_tick_away_from_the_lattice_ignore_jitter(seed: int) -> None:
    """Source frames sit on the compositor lattice, as measured: a 4 Hz wave must give exactly 7/8-frame
    gaps (4 flashes per second), however each timestamp jitters by up to 2 ms."""
    stamps, states = jittered_square_wave(seed, hz=4, seconds=4, jitter_s=0.002)
    origin, note = sample_origin(stamps, 60)
    assert note.startswith("source frames sit at phase ")
    sampled = [states[i] for i in resample_indices(stamps, origin, 60, 230)]
    changes = [i for i in range(1, len(sampled)) if sampled[i] != sampled[i - 1]]
    gaps = [b - a for a, b in zip(changes, changes[1:], strict=False)]
    assert set(gaps) <= {7, 8} and all(a + b == 15 for a, b in zip(gaps, gaps[1:], strict=False)), gaps
    # Every sample is at least 6 ms (half a tick less the jitter) away from every source frame.
    samples = [origin + i / 60 for i in range(230)]
    assert min(abs(s - t) for s in samples for t in stamps) > 0.006


def test_samples_fall_back_to_a_quarter_frame_without_a_lattice() -> None:
    tick = 1 / 60
    origin, note = sample_origin([10.0, 10 + 1.25 * tick, 10 + 2.5 * tick, 10 + 3.75 * tick], 60)
    assert origin == pytest.approx(10.0 + 0.25 / 60)
    assert note.startswith("source frames agree on no 60 Hz lattice phase")


def test_strategy_a_checks_every_captured_second() -> None:
    """Frame 0, the first frame of each second and the last frame (waapi_step.py, step 7)."""
    from capture.strategies.waapi_step import is_checkpoint

    assert [i for i in range(150) if is_checkpoint(i, 150, 60)] == [0, 60, 120, 149]
    assert [i for i in range(30) if is_checkpoint(i, 30, 60)] == [0, 29]


def test_strategy_a_docstring_lists_the_per_frame_steps_in_the_spec_order() -> None:
    """waapi_step.py's step 6 and SPEC 6.2 name the per-frame steps in one order: run_for, the
    requestAnimationFrame flush and seek pass, screenshot."""
    from capture.strategies import waapi_step

    spec = (Path(__file__).resolve().parents[2] / "docs" / "SPEC.md").read_text(encoding="utf-8")
    spec_a = spec[spec.index("* **A `waapi_step` (default).**") : spec.index("* **B `virtual_time`.**")]
    doc = waapi_step.__doc__ or ""
    step_6 = doc[doc.index("6. Per frame") : doc.index("7. Checks")]
    for text in (spec_a[spec_a.index("Per frame") :], step_6):
        positions = [text.index(word) for word in ("run_for", "requestAnimationFrame", "seek", "screenshot")]
        assert positions == sorted(positions), text
    assert "names only the first and last frames" not in doc


def test_unknown_strategy_name_is_rejected_before_any_browser_work() -> None:
    with pytest.raises(ValueError):
        capture("ignored.html", strategy="no_such_strategy")


# ------------------------------------------------------------------------------- pure: network wait (strategy A)
class _StandInRequest:
    def __init__(self, url: str) -> None:
        self.url = url


class _StandInNetworkPage:
    """Records the handlers NetworkTracker registers; each wait_for_timeout runs one scheduled action."""

    def __init__(self) -> None:
        self.handlers: dict[str, object] = {}
        self.scheduled: list[object] = []
        self.waits = 0

    def on(self, event: str, handler: object) -> None:
        self.handlers[event] = handler

    def wait_for_timeout(self, _ms: float) -> None:
        self.waits += 1
        if self.scheduled:
            self.scheduled.pop(0)()


def test_network_wait_returns_once_requests_finish() -> None:
    page = _StandInNetworkPage()
    tracker = network_wait.NetworkTracker()
    tracker.attach(page)
    request = _StandInRequest("http://127.0.0.1/a.png")
    page.handlers["request"](request)
    assert tracker.in_flight == 1
    page.scheduled = [lambda: None, lambda: page.handlers["requestfinished"](request)]
    tracker.wait_for_idle(page)
    assert tracker.in_flight == 0 and page.waits == 2
    assert tracker.take_abandoned() == []
    tracker.wait_for_idle(page)  # nothing in flight: no waiting at all
    assert page.waits == 2


def test_network_wait_gives_up_on_a_request_that_never_finishes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(network_wait, "NETWORK_WAIT_MS", 0)
    page = _StandInNetworkPage()
    tracker = network_wait.NetworkTracker()
    tracker.attach(page)
    page.handlers["request"](_StandInRequest("http://127.0.0.1/stream"))
    tracker.wait_for_idle(page)
    assert tracker.in_flight == 0
    assert tracker.take_abandoned() == ["http://127.0.0.1/stream"]
    assert tracker.take_abandoned() == []  # each request is reported once


# ------------------------------------------------------------------------------- pure: screenshot retry (strategy A)
class _StandInShotPage:
    """Answers each screenshot() with the next outcome: bytes, or an exception to raise."""

    def __init__(self, *outcomes: bytes | Exception) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[dict[str, object]] = []

    def screenshot(self, **options: object) -> bytes:
        self.calls.append(options)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def test_a_stalled_screenshot_is_requested_again_and_noted() -> None:
    """ADR-053: the first request times out, the second answers; the retry is a note, not an uncertainty."""
    from capture.strategies.base import SCREENSHOT_TIMEOUT_MS, take_screenshot
    from capture.strategies.waapi_step import _Tally

    page = _StandInShotPage(PlaywrightTimeoutError("Timeout 10000ms exceeded."), b"png")
    tally = _Tally()
    assert take_screenshot(page, tally.screenshot_retries) == b"png"
    assert [call["timeout"] for call in page.calls] == [SCREENSHOT_TIMEOUT_MS, SCREENSHOT_TIMEOUT_MS] == [10_000, 10_000]
    assert tally.screenshot_retries.used == 1
    assert any(note.startswith("screenshot retried 1 time(s): ") for note in tally.notes()), tally.notes()
    assert tally.uncertainties() == []


def test_a_screenshot_that_stalls_every_time_raises() -> None:
    """Every attempt times out: the TimeoutError propagates (strategy A errors and auto falls through)."""
    from capture.strategies.base import SCREENSHOT_ATTEMPTS, ScreenshotRetries, take_screenshot

    retries = ScreenshotRetries()
    page = _StandInShotPage(*(PlaywrightTimeoutError("Timeout 10000ms exceeded.") for _ in range(SCREENSHOT_ATTEMPTS)))
    with pytest.raises(PlaywrightTimeoutError):
        take_screenshot(page, retries)
    assert len(page.calls) == SCREENSHOT_ATTEMPTS == 2
    assert retries.used == SCREENSHOT_ATTEMPTS - 1


def test_screenshot_retries_are_capped_per_capture() -> None:
    """ADR-053: one capture retries at most SCREENSHOT_RETRIES_PER_CAPTURE stalled pictures in all; the next
    stalled picture raises at once, without a retry, so a page that stalls on every frame cannot cost 10 s per
    frame. Frames and double-shot pictures share the budget."""
    from capture.frames import rgb_to_png
    from capture.strategies.base import (
        SCREENSHOT_RETRIES_PER_CAPTURE,
        FrameSink,
        double_shot_matches,
        take_screenshot,
    )
    from capture.strategies.waapi_step import _Tally

    stall = PlaywrightTimeoutError("Timeout 10000ms exceeded.")
    second = rgb_to_png(np.zeros((4, 4, 3), dtype=np.uint8))
    tally = _Tally()
    assert SCREENSHOT_RETRIES_PER_CAPTURE == 2
    page = _StandInShotPage(stall, b"frame0", stall, second, stall, b"never asked")
    page.wait_for_timeout = lambda ms: None  # the double-shot's real-time gap
    assert take_screenshot(page, tally.screenshot_retries) == b"frame0"
    assert double_shot_matches(page, FrameSink.hash_png(second), tally.screenshot_retries)
    with pytest.raises(PlaywrightTimeoutError, match="already used its 2 screenshot retries"):
        take_screenshot(page, tally.screenshot_retries)
    assert len(page.calls) == 5  # stall, retry, stall, retry, stall: no third retry
    assert page.outcomes == [b"never asked"]
    assert tally.screenshot_retries.used == 2
    assert any(note.startswith("screenshot retried 2 time(s): ") and "once" not in note for note in tally.notes())


# ------------------------------------------------------------------------------- pure: BeginFrameControl probe
class _StandInSession:
    """Records detach() calls; answers the two DevTools commands the probe sends."""

    def __init__(self, log: list[str], refuse: bool) -> None:
        self._log, self._refuse = log, refuse

    def send(self, method: str, _params: dict[str, object] | None = None) -> dict[str, object]:
        if method == "Target.createTarget" and self._refuse:
            raise PlaywrightError("BeginFrameControl is not supported on this stand-in")
        return {"targetInfo": {"browserContextId": "stand-in"}}

    def detach(self) -> None:
        self._log.append("detach")


class _StandInBrowser:
    """Just enough of a Playwright Browser for ``begin_frame_unavailable``; counts contexts and sessions."""

    def __init__(self, version: str, refuse: bool) -> None:
        self.browser_type = SimpleNamespace(name="chromium")
        self.version = version
        self.log: list[str] = []
        self._refuse = refuse

    def _session(self, *_args: object) -> _StandInSession:
        self.log.append("session")
        return _StandInSession(self.log, self._refuse)

    def new_context(self) -> SimpleNamespace:
        self.log.append("context")
        return SimpleNamespace(new_page=lambda: object(), new_cdp_session=self._session, close=lambda: None)

    def new_browser_cdp_session(self) -> _StandInSession:
        return self._session()


@pytest.mark.parametrize("refuse", [False, True])
def test_begin_frame_probe_remembers_both_answers_and_detaches_its_sessions(monkeypatch: pytest.MonkeyPatch, refuse: bool) -> None:
    monkeypatch.setattr(vt, "_BEGIN_FRAME_PROBED", {})
    browser = _StandInBrowser(version=f"stand-in-{refuse}", refuse=refuse)
    first = vt.begin_frame_unavailable(browser)  # type: ignore[arg-type]
    second = vt.begin_frame_unavailable(browser)  # type: ignore[arg-type]
    assert first == second
    assert (first is None) is (not refuse)
    # One probe only (the second answer came from memory), and both of its sessions were detached.
    assert browser.log.count("context") == 1
    assert browser.log.count("session") == 2 == browser.log.count("detach")


# ------------------------------------------------------------------------------- browser: B
@pytest.fixture(scope="module")
def begin_frame_reason(browser: Browser) -> str | None:
    """Measured once per session: ``None`` when BeginFrameControl works, else Chromium's own error."""
    return vt.begin_frame_unavailable(browser)


@pytest.mark.browser
def test_begin_frame_support_is_probed_without_hanging(browser: Browser, begin_frame_reason: str | None) -> None:
    print(f"[measured] BeginFrameControl: {'supported' if begin_frame_reason is None else begin_frame_reason}")
    if begin_frame_reason is not None:
        assert begin_frame_reason.startswith(vt.BEGIN_FRAME_UNAVAILABLE)
    # The answer is remembered, so the second call costs nothing and says the same thing.
    assert vt.begin_frame_unavailable(browser) == begin_frame_reason


@pytest.mark.browser
def test_budget_mode_self_test_is_measured_once(browser: Browser, begin_frame_reason: str | None) -> None:
    """The budget-only self-test answers (and remembers) whether rendering stands still at a fixed virtual instant."""
    verdict = vt.budget_mode_unusable(browser)
    print(f"[measured] budget-only virtual time: {'usable' if verdict is None else verdict}")
    assert verdict is None or isinstance(verdict, str) and verdict
    assert vt.budget_mode_unusable(browser) == verdict


def _capture_b(pages_dir: Path, page: str, out: Path, browser: Browser, duration_s: float) -> CaptureResult:
    return capture(str(pages_dir / page), duration_s=duration_s, fps=FPS, strategy="virtual_time", out_dir=out, browser=browser)


@pytest.mark.browser
@pytest.mark.parametrize("page", CONTROLLABLE_PAGES)
def test_virtual_time_two_captures_are_identical_and_agree_with_a(
    pages_dir: Path, tmp_path_factory: pytest.TempPathFactory, browser: Browser, page: str
) -> None:
    first = _capture_b(pages_dir, page, tmp_path_factory.mktemp("b1"), browser, 2)
    second = _capture_b(pages_dir, page, tmp_path_factory.mktemp("b2"), browser, 2)
    for run in (first, second):
        print(f"[wall] virtual_time {page} 2 s @ {FPS} fps: {run.meta.wall_time_s:.2f} s, {run.meta.frame_count} frames, deterministic={run.meta.deterministic}")
        assert run.meta.strategy == "virtual_time"
    differing = sum(1 for a, b in zip(first.meta.frame_hashes, second.meta.frame_hashes, strict=False) if a != b)
    print(f"[measured] virtual_time {page}: identical hash lists = {first.meta.frame_hashes == second.meta.frame_hashes} ({differing} differing frames)")
    if not (first.meta.deterministic and second.meta.deterministic):
        # Measured, not assumed: strategy B itself reports why it cannot stand behind these frames here
        # (no BeginFrameControl on this platform, see the module docstring of virtual_time.py).
        reason = note_matching(first.meta.notes + second.meta.notes, r"budget-only|double-shot|beginFrame unavailable|page.screenshot did not return|readyState|virtual time")
        pytest.skip(f"strategy B is not deterministic on this machine: {reason}")
    assert first.meta.frame_count == second.meta.frame_count == 2 * FPS
    assert first.meta.frame_hashes == second.meta.frame_hashes
    assert first.meta.hash == second.meta.hash
    reference = capture(str(pages_dir / page), duration_s=2, fps=FPS, strategy="waapi_step", out_dir=tmp_path_factory.mktemp("a"), browser=browser)
    assert centre_states(first.frames) == centre_states(reference.frames)


# ------------------------------------------------------------------------------- browser: C
@pytest.mark.browser
def test_realtime_capture_of_css_page(pages_dir: Path, tmp_path: Path, browser: Browser) -> None:
    result = capture(str(pages_dir / "css_keyframes.html"), duration_s=2, fps=FPS, strategy="realtime", out_dir=tmp_path, browser=browser)
    meta = result.meta
    print(f"[wall] realtime css_keyframes 2 s @ {FPS} fps: {meta.wall_time_s:.2f} s")
    assert meta.strategy == "realtime"
    assert meta.deterministic is False
    assert meta.fallback_reason is None
    assert meta.frame_count == 2 * FPS == len(result.frames) == len(meta.frame_hashes)
    assert result.frames[0].shape == (768, 1024, 3)

    raw_note = note_matching(meta.notes, r"^\d+ screencast frame\(s\) received over [0-9.]+ s of real time$")
    raw_frames = int(raw_note.split()[0])
    print(f"[measured] {raw_note}; {note_matching(meta.notes, r'^effective source rate')}")
    assert raw_frames >= 2
    note_matching(meta.notes, r"frames may have been missed")
    note_matching(meta.notes, r"^resampled to 60 fps by holding the latest frame at or before each t_i")

    states = centre_states(result.frames)
    assert all(state in ("black", "white") for state in states), states
    assert "black" in states and "white" in states


@pytest.mark.browser
def test_realtime_static_page_holds_one_frame(pages_dir: Path, tmp_path: Path, browser: Browser) -> None:
    result = capture(str(pages_dir / "static.html"), duration_s=0.5, fps=FPS, strategy="realtime", out_dir=tmp_path, browser=browser)
    assert result.meta.strategy == "realtime"
    assert result.meta.deterministic is False
    assert result.meta.frame_count == 30
    assert len(set(result.meta.frame_hashes)) == 1
    note_matching(result.meta.notes, r"^\d+ screencast frame\(s\) received")
    note_matching(result.meta.notes, r"29 output frame\(s\) repeat their predecessor")


# ------------------------------------------------------------------------------- browser: fall-through
@pytest.mark.browser
def test_gif_page_under_auto_skips_strategy_a(pages_dir: Path, tmp_path: Path, browser: Browser) -> None:
    result = capture(str(pages_dir / "gif.html"), duration_s=1, fps=FPS, out_dir=tmp_path, browser=browser)
    meta = result.meta
    print(f"[wall] auto gif.html 1 s @ {FPS} fps: {meta.wall_time_s:.2f} s ({meta.strategy})")
    assert meta.strategy in ("virtual_time", "realtime")
    assert any(entry.startswith("animated-gif: ") for entry in meta.uncontrollable), meta.uncontrollable
    assert meta.fallback_reason is not None
    assert meta.fallback_reason.startswith("waapi_step skipped: uncontrollable content: animated-gif")
    chain = note_matching(meta.notes, r"^strategy chain: waapi_step \(skipped\) -> ")
    assert chain.endswith(f"{meta.strategy} (used)")
    assert meta.frame_count == FPS

    states = centre_states(result.frames)
    gif_advances = "black" in states and "white" in states
    print(f"[measured] gif.html under {meta.strategy}: the GIF advances between frames = {gif_advances}")
    if meta.strategy == "realtime":
        assert meta.deterministic is False
        # B either cannot take budget-only pictures here (self-test) or, in budget-only mode, steps aside
        # for the uncontrollable GIF like strategy A does.
        assert re.search(r"virtual_time (failed|skipped): ", meta.fallback_reason), meta.fallback_reason
        note_matching(meta.notes, r"^virtual_time (failed|skipped): ")
        assert gif_advances  # a real-time recording of a 4.17 Hz GIF must show both of its frames
    else:
        # Strategy B stood behind its frames (BeginFrameControl platform): report whether the GIF
        # follows virtual time, the open question in STATUS.md; either answer is a measurement.
        assert meta.deterministic is True


@pytest.mark.browser
def test_gif_under_forced_virtual_time_is_measured(pages_dir: Path, tmp_path: Path, browser: Browser, begin_frame_reason: str | None) -> None:
    """Forced B on the GIF page: does the GIF advance, and does B stand behind the frames?"""
    result = capture(str(pages_dir / "gif.html"), duration_s=1, fps=FPS, strategy="virtual_time", out_dir=tmp_path, browser=browser)
    meta = result.meta
    assert meta.strategy == "virtual_time"
    states = centre_states(result.frames)
    gif_advances = "black" in states and "white" in states
    print(f"[measured] gif.html under forced virtual_time: frames={meta.frame_count} deterministic={meta.deterministic} gif advances={gif_advances}")
    print(f"[measured] gif.html centre states under forced virtual_time: {''.join(s[0] for s in states)}")
    if begin_frame_reason is not None and vt.budget_mode_unusable(browser) is not None:
        # Budget-only mode on a Chromium whose rendering follows the real clock: B must say so.
        assert meta.deterministic is False
        note_matching(meta.notes, r"^" + re.escape(vt.BUDGET_MODE_UNUSABLE))


@pytest.mark.browser
def test_forced_strategy_is_honoured(pages_dir: Path, tmp_path_factory: pytest.TempPathFactory, browser: Browser) -> None:
    forced_c = capture(str(pages_dir / "static.html"), duration_s=0.5, fps=FPS, strategy="realtime", out_dir=tmp_path_factory.mktemp("c"), browser=browser)
    assert forced_c.meta.strategy == "realtime"
    assert forced_c.meta.fallback_reason is None

    forced_a = capture(str(pages_dir / "gif.html"), duration_s=0.5, fps=FPS, strategy="waapi_step", out_dir=tmp_path_factory.mktemp("a"), browser=browser)
    assert forced_a.meta.strategy == "waapi_step"
    assert forced_a.meta.frame_count == 30
    assert forced_a.meta.fallback_reason is None
    note_matching(forced_a.meta.notes, r"^waapi_step was used although uncontrollable content is present: animated-gif")
    # Known-uncontrollable content is never reported as a deterministic capture.
    assert forced_a.meta.deterministic is False

    forced_b = capture(str(pages_dir / "css_keyframes.html"), duration_s=0.5, fps=FPS, strategy="virtual_time", out_dir=tmp_path_factory.mktemp("b"), browser=browser)
    assert forced_b.meta.strategy == "virtual_time"
    assert forced_b.meta.fallback_reason is None
    if forced_b.meta.frame_count == 0:
        assert forced_b.meta.deterministic is False
        note_matching(forced_b.meta.notes, r"beginFrame unavailable|budget-only|page.screenshot did not return|readyState|virtual time")
    else:
        assert forced_b.meta.frame_count == 30
    if vt.begin_frame_unavailable(browser) is not None:
        note_matching(forced_b.meta.notes, r"^" + re.escape(vt.BEGIN_FRAME_UNAVAILABLE))
