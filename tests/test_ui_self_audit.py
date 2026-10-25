"""FlashTrace audits its own UI (docs/DECISIONS.md ADR-024, web/MOTION.md section 7).

The web viewer may use motion only if it never flashes more than 3 times a second. This module
proves that with FlashTrace itself: each scenario loads the built UI (``web/out``, served with a
mocked API by ``tests/ui_mock_server.py``), drives it with a scripted interaction (mashing toggles,
dialogs, navigation and history faster than any person would), captures it with ``capture.capture``
at 60 fps and analyses the frames with ``engine.analyze``. Every scenario must PASS; the control
(C0, a 5 Hz strobe) must FAIL, so a PASS is not vacuous.

Each scenario also proves it really ran (enough separate runs of changing frames) and is
reproducible (two captures give the same ``meta.hash``). The measured rates go to the test log
(``record_property`` and a summary table), never into docs: figures live only in docs/RESULTS.md.

Script timing: awaited waits run in the capture's pre-roll and are not recorded; calls scheduled
with ``every``/``setTimeout`` fire during the recorded frames, because the page clock is the
capture's fake clock.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from tests.ui_mock_server import DEFAULT_FIXTURES, DEFAULT_OUT, IDS, serve

pytestmark = pytest.mark.browser

if not (DEFAULT_OUT / "index.html").is_file():
    pytest.skip(
        "web/out is missing: run `npm run build` in web/ first (the self-audit audits the built UI)",
        allow_module_level=True,
    )

FPS = 60
DESKTOP = (1280, 900)
PHONE = (390, 844)

# Helpers every script gets (web/MOTION.md section 7).
PRELUDE = r"""
// The capture pumps the pre-roll in 16 ms steps from here, and frame 0 is the end of the step in which this script
// finishes. Recorded actions are scheduled from frame 0, not from "now": how long the awaited waits take depends on
// when data arrived, so "now" can fall anywhere inside a step, and the actions would sit at different offsets from
// frame 0 in two captures of the same scenario. Half a step later still, so no action is due exactly on the boundary
// between the pre-roll and the recording (which side a timer due at a step's very end lands on is not stable).
const T0 = performance.now();
const PUMP_MS = 16;
const toFrame0 = () => { const now = performance.now(); return T0 + PUMP_MS * Math.ceil((now - T0) / PUMP_MS) - now + PUMP_MS / 2; };
const at = (ms, act) => setTimeout(act, toFrame0() + ms);
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
async function until(find, ms = 4000) { for (let t = 0; t <= ms; t += 50) { const el = find(); if (el) return el; await wait(50); } throw new Error("self-audit: element not found"); }
const byText = (text, root = document) => [...root.querySelectorAll("button, a")].find((el) => el.textContent.trim().startsWith(text));
const every = (ms, count, act) => { const lead = toFrame0(); for (let i = 0; i < count; i++) setTimeout(() => act(i), lead + i * ms); };
// Resolve elements in the awaited pre-roll and keep the handle: labels change ("Copy CSS" -> "Copied").
const byLabel = (text) => [...document.querySelectorAll("label")].find((l) => l.textContent.trim().startsWith(text))?.control;
const jump = (y) => window.scrollTo({ top: y, behavior: "instant" }); // never smooth: it runs outside the fake clock
// A pointer-like click. el.click() has detail 0, which the UI reads as a keyboard click and answers
// without movement; detail 1 exercises the glides and the ghost, the worst case for flashing.
const press = (el) => el && el.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, view: window, detail: 1, button: 0 }));
const into = (el, offset = 120) => jump(el.getBoundingClientRect().top + window.scrollY - offset);
const settled = () => wait(800); // every choreography ends within 600 ms
const reportReady = () => until(() => document.getElementById("verdict-heading"));
const firstDelete = () => [...document.querySelectorAll("[data-delete]")].find((b) => b.getClientRects().length > 0);
"""

# Pass setups, run first in the pre-roll. capture() always emulates light mode and no reduced motion,
# so dark mode and the reduced path go through the attributes globals.css and MotionRoot honour.
PASS_SETUP = {
    "1280-light": "",
    "390-light": "",
    "1280-dark": 'document.documentElement.dataset.theme = "dark";',
    "1280-reduce": 'document.documentElement.dataset.motion = "reduce";',
}
PASS_VIEWPORT = {"1280-light": DESKTOP, "390-light": PHONE, "1280-dark": DESKTOP, "1280-reduce": DESKTOP}


@dataclass(frozen=True)
class Scenario:
    name: str
    path: str
    duration_s: float
    script: str  # pre-roll (awaited) plus the recorded part (scheduled)
    min_runs: int  # separate runs of changing frames that prove it ran
    proves: str
    expect: str = "PASS"
    server: dict[str, Any] = field(default_factory=dict)
    passes: tuple[str, ...] = ("1280-light",)
    # For a mash whose changes merge into one long run (a glide retargeted every 40 ms), the run count
    # cannot tell a finished script from one that died early; the last change must come this late.
    min_last_change_s: float = 0.0


EXTRA = ("1280-light", "390-light", "1280-dark")

SCENARIOS = [
    Scenario("E1-new-audit-entrance", "/", 3, "", 1, "form arrival, nothing hidden server-side", passes=EXTRA),
    Scenario(
        "E2-route-to-history", "/", 3,
        """
        const hist = await until(() => byText("History"));
        await until(() => byText("Run audit")); await settled();
        at(300, () => press(hist));
        """,
        1, "route entrance, nav underline glide",
    ),
    Scenario("E3-running-handoff", f"/run/?id={IDS['RUNNING']}", 8, "", 5,
             "fill tween, ticker, check draws, hold, report entrance"),
    Scenario("E4-report-entrance", f"/report/?id={IDS['FAIL']}", 3, "", 1,
             "banner group with the key figures, seal", passes=EXTRA),
    # E4 under reduced motion. Loaded data is applied on the page clock, so a report opened directly is complete at
    # frame 0, and with nothing animating there is nothing to record. Opening it from History during the recording
    # captures the arrival itself (route change, load, instant final state).
    Scenario(
        "E4r-report-arrival", "/history/", 3,
        f"""
        const open = await until(() => [...document.querySelectorAll("a")].find((a) => (a.getAttribute("href") || "").includes("{IDS['FAIL']}")));
        await settled();
        at(300, () => press(open));
        """,
        1, "report arrival under reduced motion: instant, nothing hidden", passes=("1280-reduce",),
    ),
    Scenario(
        "E5-report-scroll", f"/report/?id={IDS['FAIL']}", 4,
        """
        await reportReady(); await settled();
        const ids = ["timeline-heading", "evidence-heading", "where-heading", "fixes-heading", "violations-heading"];
        const tops = ids.map((id) => document.getElementById(id).getBoundingClientRect().top + window.scrollY - 24);
        every(700, 5, (i) => jump(tops[i]));
        """,
        5, "in-view bars against a limit line that is already drawn, then the spans",
    ),
    # Open the viewer, step as soon as the limiter allows, close as soon as the second step is on screen (the check
    # runs every 33 ms, so the close lands 0 to 2 frames after the step), reopen as soon as the open gate allows.
    # Every step is a full black and white swap (frames="alternate"). The old F1 (open/close every 120 ms) always
    # closed the viewer before its first allowed step, so it never stepped.
    Scenario(
        "F1-dialog-mash", f"/report/?id={IDS['FAIL']}", 4,
        """
        await reportReady();
        const thumb = (await until(() => { const t = [...document.querySelectorAll("button")].filter((b) => b.textContent.trim().startsWith("Enlarge")); return t.length > 1 ? t : null; }))[1];
        into(thumb, 160); await settled();
        let seen = 0, src = null;
        every(33, 120, () => {
          const d = document.querySelector("dialog[open]");
          if (!d) { seen = 0; src = null; press(thumb); return; }
          const now = d.querySelector("img")?.getAttribute("src");
          if (src === null) src = now; else if (now !== src) { seen += 1; src = now; }
          press(byText(seen >= 2 ? "Close" : "Next frame", d));
        });
        """,
        3, "open gate, veil, ghost, step limiter on full black and white swaps, instant close",
        server={"frames": "alternate"}, passes=EXTRA, min_last_change_s=2.5,
    ),
    Scenario(
        "F2-heatmap-autorepeat", f"/report/?id={IDS['FAIL']}", 3,
        """
        await reportReady();
        const overlay = await until(() => byText("Heatmap overlay"));
        into(document.querySelector("figure"), 40); await settled();
        every(33, 90, () => press(overlay));
        """,
        3, "350 ms gate plus crossfade", passes=(*EXTRA, "1280-reduce"),
    ),
    Scenario(
        "F3-culprit-mash", f"/report/?id={IDS['FAIL']}", 3,
        """
        await reportReady();
        const outline = [1, 2, 3].map((n) => document.querySelector(`button[aria-label^="Outline culprit ${n} "]`));
        into(document.querySelector("figure"), 40); await settled();
        every(50, 60, (i) => press(outline[i % 3]));
        """,
        2, "outline glide plus gate, caption height constant", passes=EXTRA, min_last_change_s=2.0,
    ),
    # One Outline button clicked every 30 ms: its own selection used to re-wrap the caption at 390 px and move the
    # button itself 21 px (FAIL 4/s).
    Scenario(
        "F3b-outline-autoclick", f"/report/?id={IDS['FAIL']}", 3,
        """
        await reportReady();
        const outline = document.querySelector('button[aria-label^="Outline culprit 1 "]');
        into(document.querySelector("figure"), 40); await settled();
        every(30, 90, () => press(outline));
        """,
        3, "one toggle on the shared gate, nothing below the figure moves", passes=EXTRA, min_last_change_s=2.0,
    ),
    # Round-robin over every control that changes the report's figure area: overlay, data table, Outline buttons and
    # culprit rows. With separate gates this measured 12 flashes a second at 390 px.
    Scenario(
        "F3c-report-round-robin", f"/report/?id={IDS['FAIL']}", 3,
        """
        await reportReady();
        const overlay = await until(() => byText("Heatmap overlay"));
        const table = byText("Show data table");
        const outline = [1, 2, 3].map((n) => document.querySelector(`button[aria-label^="Outline culprit ${n} "]`));
        const rows = outline.map((b) => b.closest("tr"));
        const all = [overlay, table, outline[0], rows[1], outline[2], rows[0]];
        into(document.querySelector("figure"), 40); await settled();
        every(30, 90, (i) => press(all[i % all.length]));
        """,
        3, "one large-area gate for overlay, table and culprit selection", passes=EXTRA, min_last_change_s=2.0,
    ),
    Scenario(
        "F4-history-delete", "/history/", 6,
        """
        window.confirm = () => true;
        await until(firstDelete); await settled();
        every(100, 60, () => press(firstDelete()));
        """,
        3, "delete gate, exit fade, instant gap close over a viewport full of rows", server={"jobs": 24},
        passes=EXTRA, min_last_change_s=4.0,
    ),
    Scenario(
        "F4b-last-delete", "/history/", 3,
        """
        window.confirm = () => true;
        await until(firstDelete); await settled();
        at(300, () => press(firstDelete()));
        """,
        1, "exit, empty state, heading focus", server={"jobs": 1}, passes=EXTRA,
    ),
    # One ordinary delete with the default list. At 390 px the old gap-closing glide slid the full-width cards up
    # by about 195 px: FAIL, 7 events in a quarter of a second.
    Scenario(
        "F4c-single-delete", "/history/", 3,
        """
        window.confirm = () => true;
        await until(firstDelete); await settled();
        at(300, () => press(firstDelete()));
        """,
        1, "one delete: fade in place, then the rows below close up at once", passes=EXTRA,
    ),
    Scenario(
        "F5-preset-churn", "/", 3,
        """
        const radios = await until(() => { const r = [...document.querySelectorAll('input[name="viewport"]')]; return r.length === 5 ? r : null; });
        into(radios[0].closest("fieldset"), 40); await settled();
        every(40, 75, (i) => press(radios[i % 5]));
        """,
        1, "in-place indicator fade, the custom-size row never moves anything", passes=EXTRA, min_last_change_s=2.4,
    ),
    # Nav clicks every 100 ms: the masthead's navigation gate lets one through every 350 ms, and the route display
    # gate holds every page that mounts within 500 ms of the last display change. The final page appears 500 ms
    # after the last navigation (about 3.65 s), so the capture runs 4 s.
    Scenario(
        "F6-nav-mash", "/", 4,
        """
        const hist = await until(() => byText("History"));
        const home = byText("New audit");
        await until(() => byText("Run audit")); await settled();
        every(100, 30, (i) => press(i % 2 ? home : hist));
        """,
        3, "navigation gate, route display gate, 1000 ms route-animation gap", passes=EXTRA, min_last_change_s=3.0,
    ),
    # History, then the FlashTrace brand link 50 ms later, every 450 ms. The brand link was not gated: FAIL 8/s at
    # 390 px. It now shares the nav links' gate, so each pair collapses into one navigation.
    Scenario(
        "F6b-brand-pair", "/", 4,
        """
        const hist = await until(() => byText("History"));
        const brand = document.querySelector("a.brand");
        await until(() => byText("Run audit")); await settled();
        every(450, 7, () => { press(hist); setTimeout(() => press(brand), 50); });
        """,
        2, "one navigation gate for the brand and the nav links", passes=EXTRA,
    ),
    Scenario(
        "F7-verify", f"/report/?id={IDS['FAIL']}", 4,
        """
        await reportReady();
        const verify = await until(() => byText("Verify this fix"));
        into(verify.closest("li"), 24); await settled();
        at(300, () => press(verify));
        """,
        2, "After values fading in, cell tint, badge settle", passes=EXTRA,
    ),
    Scenario(
        "F8-copy-mash", f"/report/?id={IDS['FAIL']}", 3,
        """
        Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText: () => Promise.resolve() } });
        await reportReady();
        const copy = await until(() => byText("Copy CSS"));
        into(copy, 300); await settled();
        every(50, 60, () => press(copy));
        """,
        1, "icon morph, no replay while Copied", passes=EXTRA,
    ),
    Scenario(
        "F9-shared-gate-mash", f"/report/?id={IDS['FAIL']}", 3,
        """
        await reportReady();
        const overlay = await until(() => byText("Heatmap overlay"));
        const table = byText("Show data table");
        into(table, 160); await settled();
        every(60, 45, (i) => press(i % 2 ? overlay : table));
        """,
        3, "the shared large-area gate", passes=EXTRA,
    ),
    Scenario(
        "F10-history-traversal", "/", 3,
        """
        const hist = await until(() => byText("History"));
        const home = byText("New audit");
        await until(() => byText("Run audit")); await wait(500);
        press(hist); await until(() => location.pathname.startsWith("/history")); await wait(1200);
        press(home); await until(() => location.pathname === "/"); await until(() => byText("Run audit")); await wait(1200);
        every(33, 60, (i) => (i % 2 ? history.forward() : history.back()));
        """,
        # The burst is held blank from its second step; the final page appears 500 ms after the last step (about
        # 2.5 s in), which proves the burst ran to its end.
        1, "route display gate", passes=EXTRA, min_last_change_s=2.0,
    ),
    # Back, then Forward 50 ms later, every 450 ms. Measured from mounts, the gate showed a page that mounted just
    # after a held page appeared: blank, page, page, blank in about 100 ms, FAIL 8/s at 390 px. Measured from the
    # last display change, the whole run stays blank and the final page appears once, 500 ms after the last step.
    Scenario(
        "F10b-traversal-pairs", "/", 4,
        """
        const hist = await until(() => byText("History"));
        const home = byText("New audit");
        await until(() => byText("Run audit")); await wait(500);
        press(hist); await until(() => location.pathname.startsWith("/history")); await wait(1200);
        press(home); await until(() => location.pathname === "/"); await until(() => byText("Run audit")); await wait(1200);
        every(450, 7, () => { history.back(); setTimeout(() => history.forward(), 50); });
        """,
        2, "route display gate measured from the last display change", passes=EXTRA, min_last_change_s=3.0,
    ),
    # Reduced motion on (the footer switch), the chart scrolled into view while reduced, then the switch pressed
    # again. Groups that had been reported off screen used to vanish and replay their entrance (FAIL, 4 events, at
    # 390 px). Pressing a thumbnail afterwards shows the script ran, with motion back on.
    Scenario(
        "S1-motion-switch", f"/report/?id={IDS['FAIL']}", 3,
        """
        await reportReady();
        const sw = await until(() => byText("Reduce motion"));
        await settled();
        press(sw); await until(() => document.documentElement.dataset.motion === "reduce");
        into(document.getElementById("timeline-heading"), 24); await settled();
        const thumb = [...document.querySelectorAll("button")].find((b) => b.textContent.trim().startsWith("Enlarge"));
        at(300, () => press(sw));
        at(1200, () => press(thumb));
        """,
        1, "turning reduced motion off hides nothing and replays nothing", passes=EXTRA,
    ),
    Scenario("C0-control-strobe", "/__control/strobe.html?run=1", 2, "", 10,
             "the pipeline sees flashing at this viewport and fps", expect="FAIL"),
]

CASES = [pytest.param(s, p, id=f"{s.name}-{p}") for s in SCENARIOS for p in s.passes]


def changed_runs(hashes: list[str]) -> int:
    """Maximal runs of consecutive frames whose hash differs from the frame before."""
    runs, inside = 0, False
    for before, now in zip(hashes, hashes[1:]):
        changed = before != now
        if changed and not inside:
            runs += 1
        inside = changed
    return runs


SUMMARY: list[dict[str, Any]] = []


@pytest.fixture(scope="module")
def chromium(request: pytest.FixtureRequest) -> Iterator[Any]:
    """One headless Chromium for the module (each capture still gets a fresh context)."""
    try:
        from playwright.sync_api import sync_playwright

        from capture.capturer import launch_chromium
    except ImportError as exc:  # pragma: no cover - depends on the environment
        pytest.skip(f"Playwright is unavailable: {exc}")
    with sync_playwright() as playwright:
        try:
            browser = launch_chromium(playwright.chromium)
        except Exception as exc:  # noqa: BLE001 - any launch failure means "no Chromium here"
            pytest.skip(f"Chromium is unavailable: {exc}")
        try:
            yield browser
        finally:
            browser.close()
            _print_summary(request)


def _print_summary(request: pytest.FixtureRequest) -> None:
    reporter = request.config.pluginmanager.get_plugin("terminalreporter")
    if reporter is None or not SUMMARY:
        return
    reporter.write_line("")
    reporter.write_line("UI self-audit (FlashTrace on its own UI; measurements, not documentation):")
    reporter.write_line(f"{'case':<34} {'verdict':<8} {'general/s':>9} {'red/s':>6} {'events':>7} {'runs':>5}")
    for row in SUMMARY:
        reporter.write_line(
            f"{row['case']:<34} {row['verdict']:<8} {row['general_max_rate']:>9} {row['red_max_rate']:>6} "
            f"{row['events']:>7} {row['runs']:>5}"
        )


def _run(scenario: Scenario, pass_name: str, browser: Any, out_dir: Path) -> Any:
    from capture import capture

    server, base = serve(DEFAULT_OUT, DEFAULT_FIXTURES, **scenario.server)  # fresh state per capture
    try:
        body = PASS_SETUP[pass_name] + "\n" + scenario.script
        return capture(
            base + scenario.path,
            scenario.duration_s,
            viewport=PASS_VIEWPORT[pass_name],
            fps=FPS,
            interaction_script=PRELUDE + body if body.strip() else None,
            browser=browser,
            out_dir=out_dir,
        )
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.parametrize(("scenario", "pass_name"), CASES)
def test_ui_self_audit(scenario: Scenario, pass_name: str, chromium: Any, tmp_path: Path, record_property: Any) -> None:
    from engine import analyze

    viewport = PASS_VIEWPORT[pass_name]
    result = _run(scenario, pass_name, chromium, tmp_path / "first")
    meta = result.meta
    assert meta.deterministic, f"capture was not deterministic, so the result is only indicative: {meta.notes}"

    hashes = list(meta.frame_hashes)
    assert len(set(hashes)) > 1, "every frame is identical: the scenario did not run"
    runs = changed_runs(hashes)

    analysis = analyze(result.frames, fps=FPS, viewport=viewport)
    events = len(analysis.qualifying_general) + len(analysis.qualifying_red)
    case = f"{scenario.name}-{pass_name}"
    record_property("verdict", analysis.verdict)
    record_property("general_max_rate", analysis.general_max_rate)
    record_property("red_max_rate", analysis.red_max_rate)
    record_property("events", events)
    record_property("changed_runs", runs)
    SUMMARY.append({"case": case, "verdict": analysis.verdict, "general_max_rate": analysis.general_max_rate,
                    "red_max_rate": analysis.red_max_rate, "events": events, "runs": runs})

    # The verdict first: a flashing UI must never hide behind a "did it run" message.
    assert analysis.verdict == scenario.expect, f"{case}: {analysis.reason()}"
    assert runs >= scenario.min_runs, f"only {runs} run(s) of changing frames; expected {scenario.min_runs} or more"
    last_change = max(i for i in range(1, len(hashes)) if hashes[i] != hashes[i - 1]) / FPS
    assert last_change >= scenario.min_last_change_s, (
        f"the last change is at {last_change:.2f} s; the scripted sequence should still be running at "
        f"{scenario.min_last_change_s} s"
    )

    again = _run(scenario, pass_name, chromium, tmp_path / "second")
    assert again.meta.deterministic, f"second capture was not deterministic: {again.meta.notes}"
    differ = [i for i, (a, b) in enumerate(zip(hashes, again.meta.frame_hashes)) if a != b]
    assert again.meta.hash == meta.hash, (
        f"two captures of the same scenario differ in {len(differ)} frame(s), first {differ[:8]} "
        f"(frames kept in {tmp_path}): the UI is not reproducible"
    )
