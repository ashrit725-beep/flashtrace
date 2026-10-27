"""ADR-052: IntersectionObserver evaluated once per captured frame by strategy A.

Two kinds of test. The capture test runs ``capture()`` on a page whose content changes because an observer
reported, and requires identical frames from captures made at the same time in separate processes. The
entry tests load a page with strategy A's own set-up (fake clock and init scripts) and run a script as its
interaction script, which reads what the observers delivered; ``frame()`` there waits two 16 ms pumps of
the clock: a seek pass evaluates the observers, the next clock step delivers their entries.
"""

from __future__ import annotations

import multiprocessing as mp
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

import pytest

PAGES = Path(__file__).resolve().parents[1] / "pages"

#: Scrolls io_reveal.html 300 px every 200 ms of virtual time, to 3000 px at 2 s. The timers fire during
#: the recorded frames (the script itself finishes at once, so the pre-roll is 0 ms).
SCROLL_SCRIPT = """
for (let i = 1; i <= 10; i++) setTimeout(() => window.scrollTo({ top: i * 300, behavior: 'instant' }), i * 200);
"""

# Probe pixels of io_reveal.html at its last frame (scrolled to 3000 px): the last box, the rootMargin box and
# the right end of the threshold meter's bar, each black once revealed.
LAST_FRAME_PROBES = {"last box": (250, 400), "inset": (750, 175), "meter bar": (880, 500)}


def _capture_reveal(_: int) -> dict[str, Any]:
    """Runs in a separate process, so four captures compete for the CPU like benchmark workers do."""
    import tempfile

    from capture.capturer import capture

    with tempfile.TemporaryDirectory() as out:
        result = capture(str(PAGES / "io_reveal.html"), duration_s=3, viewport=(1024, 768), fps=60,
                         interaction_script=SCROLL_SCRIPT, out_dir=out)
        last = result.frames[len(result.frames) - 1]
        return {
            "hashes": result.meta.frame_hashes,
            "deterministic": result.meta.deterministic,
            "strategy": result.meta.strategy,
            "notes": result.meta.notes,
            "first_all_white": bool(result.frames[0].min() > 245),
            "last": {name: int(last[y, x].max()) for name, (x, y) in LAST_FRAME_PROBES.items()},
        }


@pytest.mark.browser
def test_concurrent_captures_of_reveals_on_scroll_are_identical() -> None:
    """Four captures at the same time give the same 180 frame hashes, and the reveals really happen."""
    with ProcessPoolExecutor(4, mp_context=mp.get_context("spawn")) as pool:
        runs = list(pool.map(_capture_reveal, range(4)))
    for run in runs:
        assert run["strategy"] == "waapi_step" and run["deterministic"], run["notes"]
        assert run["first_all_white"], "frame 0 shows the top of the page, where nothing is revealed"
        assert run["last"] == {name: 0 for name in LAST_FRAME_PROBES}, f"not revealed at the last frame: {run['last']}"
        assert any("IntersectionObserver entr" in note for note in run["notes"]), run["notes"]
    hashes = runs[0]["hashes"]
    assert len(hashes) == 180
    changes = sum(a != b for a, b in zip(hashes, hashes[1:]))
    assert changes >= 30, f"only {changes} frame changes: the page did not scroll and reveal"
    differing = [sum(a != b for a, b in zip(hashes, run["hashes"])) for run in runs[1:]]
    assert differing == [0, 0, 0], f"frames differing from run 0: {differing}"


# ------------------------------------------------------------------------------- entries, in a strategy-A page
FRAME_JS = "const frame = () => new Promise((resolve) => setTimeout(resolve, 32));\n"

# 800 x 600 viewport; #a is 100 px tall at y 1000, #b far below, #c at the top; #s scrolls #inner, which
# holds #s1 (not a descendant of the document scroller's containing block for #a).
ENTRY_PAGE = """<!doctype html><html><head><meta charset="utf-8"><style>
html { scroll-behavior: auto } body { margin: 0; height: 5000px }
#a, #b, #c { position: absolute; left: 10px; width: 100px; height: 100px; background: #000 }
#a { top: 1000px } #b { top: 4000px } #c { top: 10px }
#s { position: absolute; top: 200px; left: 300px; width: 200px; height: 150px; overflow: auto }
#inner { height: 600px; position: relative } #s1 { position: absolute; top: 120px; width: 50px; height: 50px }
</style></head><body><div id="a"></div><div id="b"></div><div id="c"></div>
<div id="s"><div id="inner"><div id="s1"></div></div></div>
<iframe id="f" style="position:absolute; top:300px; left:500px; width:200px; height:100px; border:0"
 srcdoc="<body style='margin:0; height:600px'><div id=t style='position:absolute; top:60px; width:50px; height:80px'></div></body>"></iframe>
</body></html>"""

# Logs every delivered entry as [callback label, target id, isIntersecting, ratio (3 decimals)].
LOG_JS = """
window.__log = [];
const $ = (id) => document.getElementById(id);
const log = (label) => function (entries, observer) {
  for (const e of entries) window.__log.push([label, e.target.id, e.isIntersecting, Math.round(e.intersectionRatio * 1000) / 1000]);
};
"""


@pytest.fixture()
def entry_page(tmp_path: Path) -> str:
    page = tmp_path / "entries.html"
    page.write_text(ENTRY_PAGE, encoding="utf-8")
    return page.as_uri()


def run_in_captured_page(browser: Any, url: str, script: str) -> Any:
    """Load ``url`` with strategy A's set-up, run ``script`` as its interaction script, return window.__log."""
    from capture.capturer import _new_context
    from capture.strategies.base import CaptureSettings
    from capture.strategies.waapi_step import WaapiStepStrategy

    settings = CaptureSettings(url=url, duration_s=1, viewport=(800, 600), fps=60)
    context = _new_context(browser, settings.viewport)
    try:
        strategy = WaapiStepStrategy()
        strategy.prepare_context(context, settings)
        page = context.new_page()
        strategy.prepare_page(page, settings)
        page.goto(url)
        strategy.run_interaction(page, FRAME_JS + LOG_JS + script)
        return page.evaluate("window.__log")
    finally:
        context.close()


@pytest.mark.browser
def test_first_evaluation_delivers_one_entry_per_target_then_only_changes(browser: Any, entry_page: str) -> None:
    """Every target gets an entry at the first evaluation after observe(), in observe() order, in one callback
    whose this and second argument are the observer; nothing more until something changes."""
    log = run_in_captured_page(browser, entry_page, """
    const calls = [];
    const io = new IntersectionObserver(function (entries, observer) {
      calls.push([entries.length, this === io, observer === io, entries.every((e) => e instanceof IntersectionObserverEntry),
                  entries.every((e) => e.time === performance.now())]);
      log('io')(entries, observer);
    });
    io.observe($('c')); io.observe($('b')); io.observe($('c'));  // observing twice changes nothing
    await frame(); await frame(); await frame();
    window.__log.push(['calls', JSON.stringify(calls)]);
    """)
    assert log == [["io", "c", True, 1], ["io", "b", False, 0], ["calls", "[[2,true,true,true,true]]"]]


@pytest.mark.browser
def test_entries_follow_threshold_crossings(browser: Any, entry_page: str) -> None:
    """An entry each time the number of thresholds reached changes. As in Chromium, isIntersecting means a
    threshold is reached: under [0.5] a target 30 % in view gets no entry when it enters, and reports
    isIntersecting false with its ratio when it drops back below 0.5."""
    log = run_in_captured_page(browser, entry_page, """
    new IntersectionObserver(log('steps'), { threshold: [1, 0, 0.5] }).observe($('a'));
    new IntersectionObserver(log('half'), { threshold: 0.5 }).observe($('a'));
    await frame();
    for (const visible of [30, 60, 70, 100, 30, 0]) {  // px of #a (100 px tall) inside the 600 px viewport
      window.scrollTo(0, 1000 - 600 + visible); await frame(); window.__log.push(['at', visible]);
    }
    """)
    assert log == [
        ["steps", "a", False, 0], ["half", "a", False, 0],
        ["steps", "a", True, 0.3], ["at", 30],
        ["steps", "a", True, 0.6], ["half", "a", True, 0.6], ["at", 60],
        ["at", 70],
        ["steps", "a", True, 1], ["at", 100],
        ["steps", "a", True, 0.3], ["half", "a", False, 0.3], ["at", 30],
        ["at", 0],  # touching the viewport's edge still intersects (zero area reaches threshold 0): no change
    ]


@pytest.mark.browser
def test_ratio_is_float32_and_reaches_a_threshold_it_lands_on(browser: Any, entry_page: str) -> None:
    """Chromium computes intersectionRatio in float32 and compares it with float32 thresholds, so 10 px of a
    100 px target reaches threshold 0.1 (whose float32 lies above the decimal) and reports the float32 ratio.
    Computed in double, 0.1 < float32(0.1): the entry came one scroll step late."""
    log = run_in_captured_page(browser, entry_page, """
    new IntersectionObserver((entries) => {
      for (const e of entries) window.__log.push([e.target.id, e.isIntersecting, e.intersectionRatio]);
    }, { threshold: [0.1, 0.3] }).observe($('a'));
    await frame();
    for (const visible of [10, 30]) {  // px of #a (100 px tall) inside the 600 px viewport
      window.scrollTo(0, 1000 - 600 + visible); await frame(); window.__log.push(['at', visible]);
    }
    """)
    assert log == [
        ["a", False, 0],
        ["a", True, 0.10000000149011612], ["at", 10],
        ["a", True, 0.30000001192092896], ["at", 30],
    ]


@pytest.mark.browser
def test_attributes_and_errors_read_back_as_in_chromium(browser: Any, entry_page: str) -> None:
    """delay reads back truncated toward zero (a negative one included), a call without new throws Chromium's
    text, IntersectionObserverEntry.length is 0 and CSS comments separate rootMargin lengths."""
    log = run_in_captured_page(browser, entry_page, """
    window.__log.push(['delay', new IntersectionObserver(() => {}, { delay: -5 }).delay,
                       new IntersectionObserver(() => {}, { delay: 150.7, trackVisibility: true }).delay]);
    try { IntersectionObserver(() => {}); window.__log.push(['no new', 'no error']); } catch (e) { window.__log.push(['no new', e.name, e.message]); }
    window.__log.push(['length', IntersectionObserver.length, IntersectionObserverEntry.length]);
    window.__log.push(['comment', new IntersectionObserver(() => {}, { rootMargin: '10px/**/20px' }).rootMargin,
                       new IntersectionObserver(() => {}, { scrollMargin: '/* a */ 5px /* open' }).scrollMargin]);
    class Sub extends IntersectionObserver {}
    const sub = new Sub(() => {});
    window.__log.push(['subclass', sub instanceof Sub, sub instanceof IntersectionObserver, sub.thresholds]);
    """)
    assert log == [
        ["delay", -5, 150],
        ["no new", "TypeError", "Failed to construct 'IntersectionObserver': Please use the 'new' operator, "
                                "this DOM object constructor cannot be called as a function."],
        ["length", 1, 0],
        ["comment", "10px 20px 10px 20px", "5px 5px 5px 5px"],
        ["subclass", True, True, [0]],
    ]


@pytest.mark.browser
def test_unobserve_disconnect_and_observing_again(browser: Any, entry_page: str) -> None:
    """unobserve() stops one target, disconnect() every target; observe() afterwards starts afresh."""
    log = run_in_captured_page(browser, entry_page, """
    const io = new IntersectionObserver(log('io'));
    io.observe($('a')); io.observe($('c'));
    await frame();
    io.unobserve($('c')); window.scrollTo(0, 2000); await frame(); window.__log.push(['unobserved c']);
    window.scrollTo(0, 500); await frame(); window.__log.push(['scrolled back']);
    io.disconnect(); window.scrollTo(0, 0); await frame(); window.__log.push(['disconnected']);
    io.observe($('c')); await frame();
    """)
    assert log == [
        ["io", "a", False, 0], ["io", "c", True, 1],
        ["unobserved c"],
        ["io", "a", True, 1], ["scrolled back"],
        ["disconnected"],
        ["io", "c", True, 1],
    ]


@pytest.mark.browser
def test_take_records_returns_queued_entries_which_are_then_not_delivered(browser: Any, entry_page: str) -> None:
    """Entries queued by an evaluation and taken with takeRecords() never reach the callback."""
    log = run_in_captured_page(browser, entry_page, """
    const io = new IntersectionObserver(log('io'));
    io.observe($('a'));
    await frame();
    window.scrollTo(0, 1000);
    window.__ftIo.evaluate();  // what strategy A's seek pass does once per frame
    const taken = io.takeRecords();
    window.__log.push(['taken', taken.map((e) => [e.target.id, e.isIntersecting]), io.takeRecords().length]);
    await frame(); await frame();
    """)
    assert log == [["io", "a", False, 0], ["taken", [["a", True]], 0]]


@pytest.mark.browser
def test_root_margin_root_bounds_and_element_roots(browser: Any, entry_page: str) -> None:
    """rootMargin grows or shrinks the root (px floored, % of the root's size truncated), an element root
    clips by its padding box, and a target outside the root's subtree or document gets empty rectangles."""
    log = run_in_captured_page(browser, entry_page, """
    const R = (r) => [r.x, r.y, r.width, r.height].map((v) => Math.round(v));
    const bounds = (label) => (entries) => { for (const e of entries) window.__log.push([label, e.target.id, e.isIntersecting, R(e.rootBounds), R(e.intersectionRect)]); };
    // The frame (top 300 px, 100 px tall, no border) clips its target (y 60 to 140) to its top 40 px.
    new IntersectionObserver(bounds('frame'), {}).observe($('f').contentDocument.getElementById('t'));
    await frame();
    const shrink = new IntersectionObserver(bounds('shrink'), { rootMargin: '-100.7px 0px' });
    window.__log.push(['rootMargin', shrink.rootMargin, new IntersectionObserver(() => {}, { rootMargin: '10% 2.5px' }).rootMargin]);
    shrink.observe($('a'));
    new IntersectionObserver(bounds('grow'), { rootMargin: '0px 0px 10% 0px' }).observe($('a'));
    new IntersectionObserver(bounds('scroller'), { root: $('s') }).observe($('s1'));
    new IntersectionObserver(bounds('outside'), { root: $('s') }).observe($('a'));
    window.scrollTo(0, 1000 - 600 + 50);  // #a: 50 px in view, at the bottom of the viewport
    await frame();
    """)
    assert log == [
        ["frame", "t", True, [0, 0, 800, 600], [0, 60, 50, 40]],  # in the frame's own coordinates
        ["rootMargin", "-101px 0px -101px 0px", "10% 2px 10% 2px"],
        ["frame", "t", False, [0, 0, 800, 600], [0, 0, 0, 0]],  # scrolled out of view with the page
        ["shrink", "a", False, [0, 101, 800, 398], [0, 0, 0, 0]],
        ["grow", "a", True, [0, 0, 800, 660], [10, 550, 100, 100]],  # the margin reaches past the viewport
        ["scroller", "s1", True, [300, -250, 200, 150], [300, -130, 50, 30]],
        ["outside", "a", False, [0, 0, 0, 0], [0, 0, 0, 0]],
    ]


@pytest.mark.browser
def test_constructor_validation_and_interface(browser: Any, entry_page: str) -> None:
    """Chromium's errors for bad arguments, and what feature checks look at."""
    log = run_in_captured_page(browser, entry_page, """
    for (const [label, make] of [['callback', () => new IntersectionObserver(1)], ['threshold', () => new IntersectionObserver(() => {}, { threshold: 2 })],
                                 ['margin', () => new IntersectionObserver(() => {}, { rootMargin: '1em' })], ['root', () => new IntersectionObserver(() => {}, { root: 5 })],
                                 ['observe', () => new IntersectionObserver(() => {}).observe('x')]]) {
      try { make(); window.__log.push([label, 'no error']); } catch (e) { window.__log.push([label, e.name]); }
    }
    const io = new IntersectionObserver(() => {}, { threshold: [1, 0.3, 0] });
    window.__log.push(['interface', 'IntersectionObserver' in window, io instanceof IntersectionObserver, Object.prototype.toString.call(io),
                       'isIntersecting' in IntersectionObserverEntry.prototype, io.thresholds, Object.isFrozen(io.thresholds)]);
    """)
    assert log == [
        ["callback", "TypeError"], ["threshold", "RangeError"], ["margin", "SyntaxError"], ["root", "TypeError"], ["observe", "TypeError"],
        ["interface", True, True, "[object IntersectionObserver]", True, [0, 0.30000001192092896, 1], True],
    ]


@pytest.mark.browser
def test_a_callback_that_throws_is_reported_and_the_others_still_run(browser: Any, entry_page: str) -> None:
    log = run_in_captured_page(browser, entry_page, """
    window.addEventListener('error', (event) => window.__log.push(['error', String(event.error && event.error.message)]));
    new IntersectionObserver(() => { throw new Error('boom'); }).observe($('c'));
    new IntersectionObserver(log('second')).observe($('c'));
    await frame();
    """)
    assert log == [["error", "boom"], ["second", "c", True, 1]]

