"""Content strategy A must control beyond document.getAnimations() and the fake clock (ADR-022), what
the page itself asks of its animations (pause, resume, a new current time, a chain started on finish),
the ablation hook (ADR-013), the interaction script and media that loads after frame 0 (SPEC 6.2).

Every page here follows the synthetic-page contract: a 4 Hz black/white square wave (125 ms black,
then 125 ms white) whose state is read from one probe pixel per frame. Each test that claims
determinism captures twice and compares the frame hashes.
"""

from __future__ import annotations

import math
import re
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar

import numpy as np
import pytest
from playwright.sync_api import Browser

from capture import CaptureResult, capture
from capture.page_scripts import RANDOM_SEED

FPS = 60
DURATION_S = 2
BOX_XY = (300, 250)           # centre of the 400x300 box at (100,100) in every synthetic page
CLOSED_BOX_XY = (300, 570)    # shadow.html: centre of the box inside the closed shadow root
FADE_XY = (700, 200)          # css_keyframes.html: centre of the 0.5 Hz #fade element


# ------------------------------------------------------------------------------- helpers
def pixel_state(frame: np.ndarray, xy: tuple[int, int]) -> str:
    """'black', 'white' or 'other[r, g, b]' for the pixel at ``xy`` (x, y)."""
    pixel = frame[xy[1], xy[0]]
    if pixel.max() < 10:
        return "black"
    if pixel.min() > 245:
        return "white"
    return f"other{pixel.tolist()}"


def expected_state(t_seconds: float) -> str:
    """The page contract: 125 ms black, 125 ms white, repeating (4 Hz)."""
    return "black" if math.floor(t_seconds * 8) % 2 == 0 else "white"


def changes(states: list[str]) -> list[int]:
    """Frame indices at which the state differs from the previous frame."""
    return [i for i in range(1, len(states)) if states[i] != states[i - 1]]


def assert_follows_square_wave(states: list[str], times: list[float] | None = None) -> None:
    """Every frame shows the expected state; a frame next to a 125 ms boundary may show either
    neighbour (one-frame edge shift), and the number of changes must be exactly right.

    ``times`` is the animation's own time (s) at each frame; by default frame i is at i / FPS.
    """
    times = times if times is not None else [i / FPS for i in range(len(states))]
    expected = [expected_state(t) for t in times]
    assert all(state in ("black", "white") for state in states), states
    boundaries = set(changes(expected))
    for i, (observed, wanted) in enumerate(zip(states, expected, strict=True)):
        if observed != wanted:
            assert i in boundaries or (i + 1) in boundaries, f"frame {i}: {observed} (expected {wanted}) away from any boundary"
    assert len(changes(states)) == len(boundaries), f"changes at {changes(states)}, expected {sorted(boundaries)}"


def note_matching(notes: list[str], pattern: str) -> str:
    """The first note matching ``pattern`` (regex); fails with all notes when absent."""
    for note in notes:
        if re.search(pattern, note):
            return note
    raise AssertionError(f"no note matches {pattern!r} in {notes}")


def capture_twice(target: Path, out_root: pytest.TempPathFactory, browser: Browser, **options) -> list[CaptureResult]:
    """The same page captured twice in fresh contexts with the same options."""
    results = []
    for run in (1, 2):
        result = capture(str(target), fps=FPS, out_dir=out_root.mktemp(f"{target.stem}-{run}"), browser=browser, **options)
        print(f"[wall] {target.name} run {run} {options.get('duration_s')} s @ {FPS} fps: {result.meta.wall_time_s:.2f} s")
        results.append(result)
    return results


def assert_identical_deterministic(results: list[CaptureResult]) -> None:
    """Both captures used strategy A, call themselves deterministic and have identical frame hashes."""
    for result in results:
        assert result.meta.strategy == "waapi_step", result.meta.fallback_reason
        assert result.meta.deterministic is True, result.meta.notes
    assert results[0].meta.frame_hashes == results[1].meta.frame_hashes
    assert results[0].meta.hash == results[1].meta.hash


def mulberry32(seed: int) -> Iterator[float]:
    """Python copy of the page's Math.random replacement (page_scripts.SEEDED_RANDOM_JS), in uint32 maths."""
    state = seed & 0xFFFFFFFF
    while True:
        state = (state + 0x6D2B79F5) & 0xFFFFFFFF
        t = ((state ^ (state >> 15)) * (1 | state)) & 0xFFFFFFFF
        t = ((t + (((t ^ (t >> 7)) * (61 | t)) & 0xFFFFFFFF)) & 0xFFFFFFFF) ^ t
        yield ((t ^ (t >> 14)) & 0xFFFFFFFF) / 4294967296


# ------------------------------------------------------------------------------- ADR-022
@pytest.mark.browser
def test_shadow_root_animations_are_seeked(pages_dir: Path, tmp_path_factory: pytest.TempPathFactory, browser: Browser) -> None:
    """Boxes inside an open and a closed shadow root follow the 4 Hz square wave exactly, twice."""
    results = capture_twice(pages_dir / "shadow.html", tmp_path_factory, browser, duration_s=DURATION_S)
    assert_identical_deterministic(results)
    for xy in (BOX_XY, CLOSED_BOX_XY):
        assert_follows_square_wave([pixel_state(frame, xy) for frame in results[0].frames])
    note_matching(results[0].meta.notes, r"^2 animation\(s\) found at frame 0")
    note_matching(results[0].meta.notes, r"^2 shadow root\(s\) \(open or closed\) searched")


@pytest.mark.browser
def test_smil_timeline_is_paused_and_set(pages_dir: Path, tmp_path_factory: pytest.TempPathFactory, browser: Browser) -> None:
    """A discrete SMIL fill toggle follows the 4 Hz square wave exactly, twice."""
    results = capture_twice(pages_dir / "smil.html", tmp_path_factory, browser, duration_s=DURATION_S)
    assert_identical_deterministic(results)
    assert_follows_square_wave([pixel_state(frame, BOX_XY) for frame in results[0].frames])
    note_matching(results[0].meta.notes, r"^1 SVG SMIL timeline\(s\) paused and set to the frame time$")


READOPT_PAGE = """<!doctype html><html><head><meta charset="utf-8"><style>
html, body { margin: 0; background: #fff; overflow: hidden; }
#box { position: absolute; left: 100px; top: 100px; width: 400px; height: 300px; background-color: #000;
       animation: ft-blink 250ms steps(1, end) infinite; }
@keyframes ft-blink { 0% { background-color: #000; } 50% { background-color: #fff; } 100% { background-color: #000; } }
</style></head><body><div id="box"></div><script>
// After 500 ms of page time, set every animation running again (as a replay button would).
setTimeout(() => document.getAnimations().forEach((animation) => animation.play()), 500);
</script></body></html>"""


@pytest.mark.browser
def test_animation_resumed_by_the_page_is_readopted(tmp_path_factory: pytest.TempPathFactory, browser: Browser) -> None:
    """play() at 500 ms would leave the box on the real clock; A pauses and seeks it again."""
    page = tmp_path_factory.mktemp("readopt") / "readopt.html"
    page.write_text(READOPT_PAGE, encoding="utf-8")
    results = capture_twice(page, tmp_path_factory, browser, duration_s=1)
    assert_identical_deterministic(results)
    assert_follows_square_wave([pixel_state(frame, BOX_XY) for frame in results[0].frames])
    # 500 ms is frame 30 at 60 fps: the timer fires while the clock is advanced to that frame.
    note_matching(results[0].meta.notes, r"re-adopted \(paused and seeked\) at 1 frame\(s\), first at frame 30$")


RANDOM_PAGE = """<!doctype html><html><head><meta charset="utf-8"><style>
html, body { margin: 0; background: #fff; overflow: hidden; }
#box { position: absolute; left: 100px; top: 100px; width: 400px; height: 300px; }
</style></head><body><div id="box"></div><script>
// The first random number becomes the red channel of the box: 0..255.
document.getElementById('box').style.backgroundColor = 'rgb(' + Math.floor(Math.random() * 256) + ', 0, 0)';
</script></body></html>"""


@pytest.mark.browser
def test_math_random_is_seeded(tmp_path_factory: pytest.TempPathFactory, browser: Browser) -> None:
    """The page's first Math.random() is the first value of mulberry32(RANDOM_SEED), in every capture."""
    page = tmp_path_factory.mktemp("random") / "random.html"
    page.write_text(RANDOM_PAGE, encoding="utf-8")
    results = capture_twice(page, tmp_path_factory, browser, duration_s=0.25)
    assert_identical_deterministic(results)
    expected_red = math.floor(next(mulberry32(RANDOM_SEED)) * 256)
    assert results[0].frames[0][BOX_XY[1], BOX_XY[0]].tolist() == [expected_red, 0, 0]
    note_matching(results[0].meta.notes, r"^Math.random replaced by a fixed-seed generator \(mulberry32, seed 0x2f6e2b1\)")


def synthetic_page(style: str, body: str, script: str) -> str:
    """A page in the synthetic-page contract: white, no margins, plus the given CSS, markup and script."""
    return (
        '<!doctype html><html><head><meta charset="utf-8"><style>html, body { margin: 0; background: #fff; overflow: hidden; } '
        f"{style}</style></head><body>{body}<script>{script}</script></body></html>"
    )


BOX_CSS = "#box { position: absolute; left: 100px; top: 100px; width: 400px; height: 300px; background-color: #000; }"
BLINK_CSS = (
    "#box { animation: ft-blink 250ms steps(1, end) infinite; } "
    "@keyframes ft-blink { 0% { background-color: #000; } 50% { background-color: #fff; } 100% { background-color: #000; } }"
)
# One 250 ms cycle of the square wave as a Web Animation: black for 125 ms, then white.
ONE_CYCLE = (
    "[{ backgroundColor: '#000', offset: 0, easing: 'steps(1, end)' }, "
    "{ backgroundColor: '#fff', offset: 0.5, easing: 'steps(1, end)' }, { backgroundColor: '#fff', offset: 1 }]"
)


def write_page(tmp_path_factory: pytest.TempPathFactory, name: str, html: str) -> Path:
    page = tmp_path_factory.mktemp(name) / f"{name}.html"
    page.write_text(html, encoding="utf-8")
    return page


# Each cycle starts the next one when it finishes, the way animation sequencers chain animations.
FINISH_CHAINS = {
    "finished-promise": f"const box = document.getElementById('box'); function run() {{ box.animate({ONE_CYCLE}, 250).finished.then(run); }} run();",
    "onfinish": f"const box = document.getElementById('box'); function run() {{ box.animate({ONE_CYCLE}, 250).onfinish = run; }} run();",
    "finish-listener": (
        "const box = document.getElementById('box'); "
        f"function run() {{ box.animate({ONE_CYCLE}, 250).addEventListener('finish', run); }} run();"
    ),
}


@pytest.mark.browser
@pytest.mark.parametrize("chain", sorted(FINISH_CHAINS))
def test_animations_chained_on_finish_keep_running(tmp_path_factory: pytest.TempPathFactory, browser: Browser, chain: str) -> None:
    """A paused animation never finishes by itself; A finishes it at its end, so the page's next
    animation starts and the 4 Hz strobe is captured as a strobe, not as one cycle and then stillness."""
    page = write_page(tmp_path_factory, chain, synthetic_page(BOX_CSS, '<div id="box"></div>', FINISH_CHAINS[chain]))
    results = capture_twice(page, tmp_path_factory, browser, duration_s=1.5)
    assert_identical_deterministic(results)
    assert_follows_square_wave([pixel_state(frame, BOX_XY) for frame in results[0].frames])
    note_matching(results[0].meta.notes, r"^\d+ animation\(s\) reached their end and were finished")


def pause_then_resume_times(count: int) -> list[float]:
    """Animation time (s) per frame for a page that pauses at 600 ms and resumes at 1000 ms."""
    times = []
    for i in range(count):
        t = i / FPS
        times.append(t if t < 0.6 else 0.6 if t < 1.0 else 0.6 + (t - 1.0))
    return times


SVG_BOX = (
    '<svg width="1024" height="768" style="position:absolute;left:0;top:0"><rect x="100" y="100" width="400" height="300" fill="#000">'
    '<animate attributeName="fill" values="#000;#fff" dur="0.25s" calcMode="discrete" repeatCount="indefinite"/></rect></svg>'
)
# The page pauses its own animation at 600 ms and resumes it at 1000 ms, three different ways.
PAGE_PAUSES = {
    "css-play-state": (
        BOX_CSS + " " + BLINK_CSS + " .stopped #box { animation-play-state: paused; }",
        '<div id="box"></div>',
        "setTimeout(() => document.body.classList.add('stopped'), 600); setTimeout(() => document.body.classList.remove('stopped'), 1000);",
    ),
    "animation-pause": (
        BOX_CSS + " " + BLINK_CSS,
        '<div id="box"></div>',
        "setTimeout(() => document.getAnimations().forEach((a) => a.pause()), 600); "
        "setTimeout(() => document.getAnimations().forEach((a) => a.play()), 1000);",
    ),
    "svg-pause": (
        "",
        SVG_BOX,
        "const svg = document.querySelector('svg'); setTimeout(() => svg.pauseAnimations(), 600); setTimeout(() => svg.unpauseAnimations(), 1000);",
    ),
}


@pytest.mark.browser
@pytest.mark.parametrize("how", sorted(PAGE_PAUSES))
def test_pauses_the_page_makes_are_honoured(tmp_path_factory: pytest.TempPathFactory, browser: Browser, how: str) -> None:
    """While the page holds its animation paused (600-1000 ms) the capture stands still, then the
    animation continues from where it stopped, as in a normal browser."""
    page = write_page(tmp_path_factory, how, synthetic_page(*PAGE_PAUSES[how]))
    results = capture_twice(page, tmp_path_factory, browser, duration_s=1.5)
    assert_identical_deterministic(results)
    states = [pixel_state(frame, BOX_XY) for frame in results[0].frames]
    assert len(set(states[36:60])) == 1, states  # frames 36-59: 600 ms to 983 ms, paused
    assert_follows_square_wave(states, pause_then_resume_times(len(states)))
    note_matching(results[0].meta.notes, r"(animation|SVG timeline)\(s\) paused by the page .*were held still at 24 frame\(s\), first at frame 36$")


@pytest.mark.browser
def test_svg_timeline_paused_by_the_page_at_load_stays_still(tmp_path_factory: pytest.TempPathFactory, browser: Browser) -> None:
    page = write_page(tmp_path_factory, "smil-paused", synthetic_page("", SVG_BOX, "document.querySelector('svg').pauseAnimations();"))
    results = capture_twice(page, tmp_path_factory, browser, duration_s=0.5)
    assert_identical_deterministic(results)
    assert {pixel_state(frame, BOX_XY) for frame in results[0].frames} == {"black"}
    note_matching(results[0].meta.notes, r"^1 SVG timeline\(s\) were already paused by the page and were left paused$")


TIMELINE_RAF = (
    "const box = document.getElementById('box'); "
    "function tick() { const t = document.timeline.currentTime / 1000; "
    "box.style.backgroundColor = Math.floor(t * 8) % 2 === 0 ? '#000' : '#fff'; requestAnimationFrame(tick); } tick();"
)


@pytest.mark.browser
def test_raf_loop_timed_by_document_timeline_follows_virtual_time(tmp_path_factory: pytest.TempPathFactory, browser: Browser) -> None:
    """document.timeline.currentTime reads the fake clock, so a rAF loop timed by it reproduces exactly."""
    page = write_page(tmp_path_factory, "timeline-raf", synthetic_page(BOX_CSS, '<div id="box"></div>', TIMELINE_RAF))
    results = capture_twice(page, tmp_path_factory, browser, duration_s=1)
    assert_identical_deterministic(results)
    assert_follows_square_wave([pixel_state(frame, BOX_XY) for frame in results[0].frames])


# Two boxes, each 200x300: #a at x 100-300, #c at x 300-500. The page starts #c half a cycle ahead.
PHASE_CSS = ".b { position: absolute; top: 100px; width: 200px; height: 300px; background-color: #000; } #a { left: 100px; } #c { left: 300px; }"
PHASE_SETTERS = {
    "currentTime": "c.currentTime = 125;",
    "startTime": "c.startTime = document.timeline.currentTime - 125;",
}


@pytest.mark.browser
@pytest.mark.parametrize("setter", sorted(PHASE_SETTERS))
def test_phase_the_page_sets_is_kept(tmp_path_factory: pytest.TempPathFactory, browser: Browser, setter: str) -> None:
    """A current time (or start time) the page assigns is the animation's phase: #c stays 125 ms ahead."""
    script = (
        f"const kf = {ONE_CYCLE}; document.getElementById('a').animate(kf, {{ duration: 250, iterations: Infinity }}); "
        f"const c = document.getElementById('c').animate(kf, {{ duration: 250, iterations: Infinity }}); {PHASE_SETTERS[setter]}"
    )
    page = write_page(tmp_path_factory, f"phase-{setter}", synthetic_page(PHASE_CSS, '<div class="b" id="a"></div><div class="b" id="c"></div>', script))
    results = capture_twice(page, tmp_path_factory, browser, duration_s=1)
    assert_identical_deterministic(results)
    frames = results[0].frames
    assert_follows_square_wave([pixel_state(frame, (200, 250)) for frame in frames])
    assert_follows_square_wave([pixel_state(frame, (400, 250)) for frame in frames], [i / FPS + 0.125 for i in range(len(frames))])


DECLARATIVE_CLOSED_ROOT = (
    '<div id="host" style="position:absolute;left:100px;top:100px"><template shadowrootmode="closed"><style>'
    "#box { width: 400px; height: 300px; background-color: #000; animation: ft-blink 250ms steps(1, end) infinite; } "
    "@keyframes ft-blink { 0% { background-color: #000; } 50% { background-color: #fff; } 100% { background-color: #000; } }"
    '</style><div id="box"></div></template></div>'
)


@pytest.mark.browser
def test_closed_shadow_root_written_in_html_is_driven(tmp_path_factory: pytest.TempPathFactory, browser: Browser) -> None:
    """A closed root from <template shadowrootmode="closed"> never passes through attachShadow; it is found
    through the DevTools protocol and its animation is seeked like any other."""
    page = write_page(tmp_path_factory, "dsd-closed", synthetic_page("", DECLARATIVE_CLOSED_ROOT, ""))
    results = capture_twice(page, tmp_path_factory, browser, duration_s=1)
    assert_identical_deterministic(results)
    assert_follows_square_wave([pixel_state(frame, BOX_XY) for frame in results[0].frames])
    note_matching(results[0].meta.notes, r"^1 closed shadow root\(s\) written in HTML found through the DevTools protocol")


# ------------------------------------------------------------------------------- ADR-013 ablation
@pytest.mark.browser
def test_suppressed_box_stands_still_while_fade_animates(pages_dir: Path, tmp_path: Path, browser: Browser) -> None:
    """suppress_selectors=['#box']: the 4 Hz box keeps its frame-0 colour, the 0.5 Hz fade still moves."""
    result = capture(
        str(pages_dir / "css_keyframes.html"), duration_s=1, fps=FPS, out_dir=tmp_path, browser=browser, suppress_selectors=["#box"]
    )
    meta = result.meta
    print(f"[wall] css_keyframes.html ablation 1 s @ {FPS} fps: {meta.wall_time_s:.2f} s")
    assert meta.strategy == "waapi_step" and meta.deterministic is True, meta.notes
    box_colours = {tuple(frame[BOX_XY[1], BOX_XY[0]].tolist()) for frame in result.frames}
    assert box_colours == {(0, 0, 0)}  # the 0% keyframe: black
    fade_values = {int(frame[FADE_XY[1], FADE_XY[0], 0]) for frame in result.frames}
    assert len(fade_values) > 30, sorted(fade_values)  # opacity ramps 0 -> 1 over this second
    note_matching(
        meta.notes,
        r"^ablation: suppressed selectors \['#box'\] \(re-applied every frame; their animations are held at their frame-0 state "
        r"by the frame loop\): 1 element\(s\) marked data-ft-suppressed, 0 media element\(s\) hidden",
    )
    note_matching(meta.notes, r"^1 animation\(s\) on suppressed elements held at their frame-0 state$")


HOLD_PAGE = """<!doctype html><html><head><meta charset="utf-8"><style>
html, body { margin: 0; background: #fff; overflow: hidden; }
/* The box's own colour is red; its animation starts at black. Frame-0 state: black. */
#box { position: absolute; left: 100px; top: 100px; width: 400px; height: 300px; background-color: #f00;
       animation: ft-blink 250ms steps(1, end) infinite; }
@keyframes ft-blink { 0% { background-color: #000; } 50% { background-color: #fff; } 100% { background-color: #000; } }
#late { position: absolute; left: 600px; top: 100px; width: 200px; height: 200px; background-color: #888; }
</style></head><body><div id="box"></div><div id="late"></div><script>
// At 200 ms of page time, script starts a 4 Hz animation on #late.
setTimeout(() => document.getElementById('late').animate(
  [{ backgroundColor: '#000' }, { backgroundColor: '#fff' }], { duration: 250, iterations: Infinity }), 200);
</script></body></html>"""


@pytest.mark.browser
def test_suppressed_animation_is_held_at_its_frame_0_state(tmp_path: Path, browser: Browser) -> None:
    """ADR-013 (a): the suppressed box shows its frame-0 keyframe (black), not its own style (red); an
    animation created after frame 0 on a suppressed element is cancelled."""
    page = tmp_path / "hold.html"
    page.write_text(HOLD_PAGE, encoding="utf-8")
    result = capture(str(page), duration_s=0.5, fps=FPS, out_dir=tmp_path / "out", browser=browser, suppress_selectors=["#box", "#late"])
    assert result.meta.strategy == "waapi_step" and result.meta.deterministic is True, result.meta.notes
    assert {tuple(frame[BOX_XY[1], BOX_XY[0]].tolist()) for frame in result.frames} == {(0, 0, 0)}
    # #late has no animation when the ablation starts, so it is hidden as script-driven (white page shows).
    assert {pixel_state(frame, (700, 200)) for frame in result.frames} == {"white"}
    note_matching(result.meta.notes, r"1 element\(s\) without animations hidden as script-driven$")
    note_matching(result.meta.notes, r"^1 animation\(s\) created after frame 0 on suppressed elements were cancelled$")


@pytest.mark.browser
def test_suppressed_script_driven_box_is_hidden(pages_dir: Path, tmp_path: Path, browser: Browser) -> None:
    """ADR-013 (b): raf.html's box is painted by a requestAnimationFrame loop, which nothing can pause, so
    suppressing it hides it (the white page shows through) instead of leaving it flashing."""
    result = capture(str(pages_dir / "raf.html"), duration_s=1, fps=FPS, out_dir=tmp_path, browser=browser, suppress_selectors=["#box"])
    assert result.meta.strategy == "waapi_step" and result.meta.deterministic is True, result.meta.notes
    assert {pixel_state(frame, BOX_XY) for frame in result.frames} == {"white"}
    note_matching(result.meta.notes, r"0 media element\(s\) hidden.*1 element\(s\) without animations hidden as script-driven$")


@pytest.mark.browser
@pytest.mark.parametrize(
    ("selector", "expected_box", "note_pattern"),
    [
        # The whole <svg> is suppressed: hidden, its timeline held at 0; the white page shows through.
        ("svg", "white", r"1 media element\(s\) hidden, 1 SVG timeline\(s\) held at 0 s, 0 SMIL animation element\(s\) removed, 0 element"),
        # Only the rect is suppressed: its <animate> is removed, so the rect keeps its own fill (#000).
        ("rect", "black", r"0 media element\(s\) hidden, 0 SVG timeline\(s\) held at 0 s, 1 SMIL animation element\(s\) removed, 0 element"),
    ],
)
def test_suppressed_smil_content_stands_still(
    pages_dir: Path, tmp_path: Path, browser: Browser, selector: str, expected_box: str, note_pattern: str
) -> None:
    result = capture(str(pages_dir / "smil.html"), duration_s=0.5, fps=FPS, out_dir=tmp_path, browser=browser, suppress_selectors=[selector])
    assert result.meta.strategy == "waapi_step" and result.meta.deterministic is True, result.meta.notes
    assert {pixel_state(frame, BOX_XY) for frame in result.frames} == {expected_box}
    note_matching(result.meta.notes, note_pattern)


@pytest.mark.browser
def test_suppressed_animated_gif_no_longer_forces_a_fallback(pages_dir: Path, tmp_path: Path, browser: Browser) -> None:
    """Hiding the only uncontrollable element lets strategy A capture the page deterministically."""
    result = capture(str(pages_dir / "gif.html"), duration_s=0.5, fps=FPS, out_dir=tmp_path, browser=browser, suppress_selectors=["#gif"])
    meta = result.meta
    assert meta.strategy == "waapi_step" and meta.deterministic is True, meta.notes
    assert meta.uncontrollable == [] and meta.fallback_reason is None
    assert {pixel_state(frame, BOX_XY) for frame in result.frames} == {"white"}  # the GIF is hidden
    note_matching(meta.notes, r"1 media element\(s\) hidden")


@pytest.mark.browser
def test_animation_ready_timeout_marks_the_capture_non_deterministic(
    pages_dir: Path, tmp_path: Path, browser: Browser, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no time allowed for animation.ready, the pause handshake times out at frame 0 (SPEC 6.2)."""
    from capture.strategies import waapi_step

    monkeypatch.setattr(waapi_step, "READY_TIMEOUT_MS", 0)
    result = capture(str(pages_dir / "css_keyframes.html"), duration_s=0.25, fps=FPS, out_dir=tmp_path, browser=browser)
    assert result.meta.strategy == "waapi_step"  # a timeout lowers confidence; it is not a reason to fall through
    assert result.meta.deterministic is False
    note_matching(result.meta.notes, r"^animation-ready wait timed out after 0 ms of real time at \d+ frame\(s\), first at frame 0; deterministic=false$")


@pytest.mark.browser
def test_invalid_suppress_selector_is_an_error(pages_dir: Path, tmp_path: Path, browser: Browser) -> None:
    with pytest.raises(ValueError, match="invalid CSS selector"):
        capture(str(pages_dir / "static.html"), duration_s=0.1, fps=FPS, out_dir=tmp_path, browser=browser, suppress_selectors=["#("])


# ------------------------------------------------------------------------------- detection (SPEC 6.2 fall-through)
GIF_BOX = "position: absolute; left: 250px; top: 200px; width: 100px; height: 100px;"


def gif_pages(gif_b64: str) -> dict[str, str]:
    """Pages that show capture/pages/anim.gif (copied next to them) in ways other than a plain <img>."""
    return {
        "before-background": f"<style>#g::before {{ content: ''; {GIF_BOX} background: url(anim.gif) 0 0 / 100px 100px; }}</style><div id='g'></div>",
        "content-url": f"<style>#g {{ {GIF_BOX} content: url(anim.gif); }}</style><div id='g'></div>",
        "list-marker": "<style>li { list-style-image: url(anim.gif); }</style><ul><li>item</li></ul>",
        "svg-image": "<svg width='200' height='200'><image href='anim.gif' width='100' height='100'/></svg>",
        "blob-url": (
            f"<img id='i' style='{GIF_BOX}' alt=''><script>const bytes = Uint8Array.from(atob('{gif_b64}'), (c) => c.charCodeAt(0));"
            "document.getElementById('i').src = URL.createObjectURL(new Blob([bytes], { type: 'image/gif' }));</script>"
        ),
        "upper-case-base64": f"<img style='{GIF_BOX}' alt='' src='data:image/gif;BASE64,{gif_b64}'>",
        "input-image": "<input type='image' src='anim.gif' alt='go'>",
    }


@pytest.mark.browser
@pytest.mark.parametrize("form", sorted(gif_pages("")))
def test_animated_gif_is_detected_wherever_the_page_shows_it(pages_dir: Path, tmp_path: Path, browser: Browser, form: str) -> None:
    """Every form must reach detection, otherwise strategy A would run with a GIF on the real clock."""
    import base64
    import shutil

    from capture.detect import CANVAS_PROBE_JS, find_uncontrollable
    from capture.page_scripts import SHADOW_ROOT_RECORDER_JS

    shutil.copy(pages_dir / "anim.gif", tmp_path / "anim.gif")
    gif_b64 = base64.b64encode((pages_dir / "anim.gif").read_bytes()).decode("ascii")
    page_path = tmp_path / f"{form}.html"
    page_path.write_text(f"<!doctype html><html><body>{gif_pages(gif_b64)[form]}</body></html>", encoding="utf-8")
    context = browser.new_context()
    try:
        context.add_init_script(CANVAS_PROBE_JS)
        context.add_init_script(SHADOW_ROOT_RECORDER_JS)
        page = context.new_page()
        page.goto(page_path.as_uri(), wait_until="load")
        uncontrollable, notes = find_uncontrollable(page, allow_local_files=True)
    finally:
        context.close()
    assert any(entry.startswith("animated-gif: ") for entry in uncontrollable), (uncontrollable, notes)


# ------------------------------------------------------------------------------- interaction script
INTERACTION = "await new Promise((resolve) => setTimeout(resolve, 100)); document.body.classList.add('ft-done');"
INTERACTION_CSS = ".ft-done #box { background-color: #fff !important; }"


@pytest.mark.browser
def test_interaction_script_waits_on_virtual_time(pages_dir: Path, tmp_path_factory: pytest.TempPathFactory, browser: Browser) -> None:
    """A script that sets a class after a 100 ms (virtual) timeout has finished before frame 0."""
    results = capture_twice(
        pages_dir / "static.html", tmp_path_factory, browser, duration_s=0.5, interaction_script=INTERACTION, extra_css=INTERACTION_CSS
    )
    assert_identical_deterministic(results)
    assert all(pixel_state(frame, BOX_XY) == "white" for frame in results[0].frames)  # black without the class
    note = note_matching(results[0].meta.notes, r"^interaction script consumed \d+ ms of virtual time before frame 0$")
    consumed = int(note.split()[3])
    assert 100 <= consumed <= 128, note  # pumped in 16 ms steps until the 100 ms timer had fired


@pytest.mark.browser
def test_interaction_waiting_for_an_animation_ends_on_virtual_time(pages_dir: Path, tmp_path_factory: pytest.TempPathFactory, browser: Browser) -> None:
    """The script awaits a 300 ms animation's finished promise. The animation is driven on virtual time
    during the pumps, so the pre-roll (and every frame after it) is the same in every run."""
    script = "await document.body.animate([{ opacity: 1 }, { opacity: 1 }], 300).finished; document.body.classList.add('ft-done');"
    results = capture_twice(pages_dir / "static.html", tmp_path_factory, browser, duration_s=0.25, interaction_script=script, extra_css=INTERACTION_CSS)
    assert_identical_deterministic(results)
    assert all(pixel_state(frame, BOX_XY) == "white" for frame in results[0].frames)
    consumed = [int(note_matching(r.meta.notes, r"^interaction script consumed \d+ ms").split()[3]) for r in results]
    assert consumed[0] == consumed[1] and 300 <= consumed[0] <= 316, consumed  # the first 16 ms pump at or after 300 ms


# ------------------------------------------------------------------------------- media loaded after frame 0
def black_png() -> bytes:
    """A 64x64 black PNG (the browser scales it to the <img> box)."""
    from io import BytesIO

    from PIL import Image

    buffer = BytesIO()
    Image.new("RGB", (64, 64), (0, 0, 0)).save(buffer, "PNG")
    return buffer.getvalue()


class _SlowHandler(BaseHTTPRequestHandler):
    """'/<name>' serves the page registered under that name; '/slow.png?delay=S' and '/slow.webm?delay=S'
    answer after S seconds of real time (the webm body is not a real video: it never needs to play)."""

    pages: ClassVar[dict[str, bytes]] = {}

    def log_message(self, *_args: object) -> None:  # keep the test output clean
        pass

    def do_GET(self) -> None:  # noqa: N802 - the name http.server calls
        path, _, query = self.path.partition("?")
        delay = float(query.split("=")[1]) if query.startswith("delay=") else 0.0
        if path == "/slow.png":
            body, kind = black_png(), "image/png"
        elif path == "/slow.webm":
            body, kind = b"\x1a\x45\xdf\xa3", "video/webm"
        else:
            body, kind = self.pages.get(path.lstrip("/"), b""), "text/html"
        try:
            time.sleep(delay)
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except OSError:  # the browser gave up on the request (the capture ended first)
            pass


@pytest.fixture()
def slow_server() -> Iterator[str]:
    """A loopback-only HTTP server for one test; yields its base URL."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _SlowHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/"
    finally:
        server.shutdown()
        server.server_close()


IMG_CSS = "#img { position: absolute; left: 100px; top: 100px; width: 400px; height: 300px; }"


@pytest.mark.browser
def test_image_loaded_after_frame_0_appears_at_its_frame(slow_server: str, tmp_path_factory: pytest.TempPathFactory, browser: Browser) -> None:
    """A timer sets an <img> src at 100 ms of page time; the server answers 0.6 s of real time later.
    The capture waits for it, so the image appears at frame 6 (100 ms) in every run."""
    script = "setTimeout(() => { document.getElementById('img').src = '/slow.png?delay=0.6'; }, 100);"
    _SlowHandler.pages["late-image"] = synthetic_page(IMG_CSS, '<img id="img" alt="">', script).encode()
    results = []
    for run in (1, 2):
        result = capture(slow_server + "late-image", duration_s=0.5, fps=FPS, out_dir=tmp_path_factory.mktemp(f"late-{run}"), browser=browser)
        results.append(result)
    assert_identical_deterministic(results)
    states = [pixel_state(frame, BOX_XY) for frame in results[0].frames]
    assert states == ["white"] * 6 + ["black"] * 24, states


@pytest.mark.browser
def test_media_that_never_arrives_makes_the_capture_non_deterministic(
    slow_server: str, tmp_path: Path, browser: Browser, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A <video> whose server does not answer within the limits: its data could arrive at any later
    frame, so the capture says deterministic=false and names the request."""
    from capture.strategies import network_wait, waapi_step

    monkeypatch.setattr(network_wait, "NETWORK_WAIT_MS", 300)
    monkeypatch.setattr(waapi_step, "MEDIA_READY_TIMEOUT_MS", 300)
    # The src is set by a timer: a <video> in the HTML would delay the load event until it answered.
    script = "setTimeout(() => { document.getElementById('img').src = '/slow.webm?delay=3'; }, 50);"
    _SlowHandler.pages["stalled-video"] = synthetic_page(IMG_CSS, '<video id="img" muted></video>', script).encode()
    result = capture(slow_server + "stalled-video", duration_s=0.25, fps=FPS, out_dir=tmp_path, browser=browser)
    assert result.meta.strategy == "waapi_step"  # lower confidence, not a reason to fall through
    assert result.meta.deterministic is False
    note = note_matching(result.meta.notes, r"^network request\(s\) still unfinished after 300 ms of real time were no longer waited for: ")
    assert "/slow.webm?delay=3" in note and note.endswith("; deterministic=false")
