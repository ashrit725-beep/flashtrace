"""Strategy B ``virtual_time``: Chromium virtual time through the DevTools protocol (SPEC.md section 6.2).

How it is meant to work, in plain words:

1. Chromium's ``Emulation.setVirtualTimePolicy`` replaces the renderer's clock with a synthetic one.
   While the policy is ``pause`` nothing in the page moves; ``advance`` with a *budget* lets exactly that
   many milliseconds of page time elapse (as fast as the CPU allows) and then pauses again, firing
   ``Emulation.virtualTimeBudgetExpired``. Per frame we grant ``1000 / fps`` ms and take a picture.
2. Pictures of a paused renderer need ``HeadlessExperimental.beginFrame``: with *BeginFrameControl* the
   compositor only draws when told, and each ``beginFrame`` call draws one frame at the frame time we
   name and can return it as a PNG. BeginFrameControl is enabled per target through
   ``Target.createTarget {enableBeginFrameControl: true}``; that call errors cleanly where the platform
   lacks it (a bare ``beginFrame`` on an ordinary target hangs instead, so it is never used as a probe).
3. Without BeginFrameControl the strategy falls back to budgets plus ``page.screenshot`` with a short
   timeout and records that in the notes.
4. The same double-shot check as strategy A: two pictures at the same virtual time must be identical.

What was measured on the development machine (macOS, Playwright 1.63, Chromium 153 headless shell and
full build; the orchestrator's ADR record has the details):

* ``Target.createTarget {enableBeginFrameControl: true}`` fails with "BeginFrameControl is not
  supported on MacOS yet" in the headless shell; the full build accepts the flag but has no
  ``HeadlessExperimental`` domain.
* While virtual time is paused the renderer runs no page tasks at all (not even a ``MessageChannel``
  message), so ``Page.captureScreenshot`` and ``page.screenshot`` never return.
* A deferred policy (``waitForNavigation``) latches onto the *next* navigation, which for a fresh page
  can be its own ``about:blank``; the policy is therefore armed in ``navigate`` right before ``goto``.
* Advancing in ``1000/60`` ms budgets is exact (120 budgets moved ``performance.now()`` by 1999.9 ms, no
  expiry event missed) and a budget expires within a few milliseconds of real time; the document load
  needs one or two extra 250 ms slices to reach ``readyState == "complete"``, after which
  ``page.screenshot`` does return (60/60 frames on the CSS and GIF pages; one hang at frame 79 of 120
  on the rAF page, caught by the 2 s timeout).
* Without BeginFrameControl the page's *clocks* are virtual (``animation.currentTime`` moved exactly
  16.67 ms per frame, a rAF-driven page drew a clean 4 Hz square wave) but *rendering* follows real time:
  the CSS/WAAPI boxes flipped every 3-4 captured frames (about 35 ms of real time per frame) and the
  animated GIF advanced at its real 120 ms cadence. Flash rates measured from such frames would be
  wrong, so budget-only mode is not a verdict-grade picture source. The double-shot check caught the
  CSS pages every time but let the GIF page through 4 times out of 6, which is why
  ``budget_mode_unusable`` measures the defect directly (two pictures 100 ms of real time apart at one
  virtual instant) once per Chromium build, and why the budget-only double-shot waits that long too.

So on that machine "auto" leaves this strategy before loading the page and moves on to strategy C; a
forced ``virtual_time`` capture still produces frames for inspection, marked ``deterministic=False``
with the measured reason. The code paths that need BeginFrameControl follow the protocol documentation
and are *not verified here*; the notes say exactly which step did or did not work, so a run on a
platform that supports it reports its own evidence.
"""

from __future__ import annotations

import base64
import json
import time
from typing import Any, ClassVar
from urllib.parse import quote

from playwright.sync_api import Browser, BrowserContext, CDPSession, Page
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from capture.page_scripts import SEEDED_RANDOM_JS, SEEDED_RANDOM_NOTE
from capture.strategies.base import (
    DOUBLE_SHOT_REAL_GAP_MS,
    CaptureSettings,
    CaptureStrategy,
    FrameSink,
    ProgressCallback,
    StrategyOutcome,
)

#: Chromium switches that make BeginFrameControl rendering reproducible (the parts of Chromium's own
#: ``--deterministic-mode`` that matter for screenshots). They are only requested when BeginFrameControl
#: is available, and then in a dedicated browser so strategy A's measurements are never taken under
#: different switches than the ones they were validated with.
LAUNCH_ARGS: tuple[str, ...] = (
    "--enable-begin-frame-control",
    "--run-all-compositor-stages-before-draw",
    "--disable-threaded-animation",
    "--disable-threaded-scrolling",
    "--disable-checker-imaging",
)

#: ``Date.now()`` inside the page starts at this wall-clock time on every run (2023-11-14T22:13:20Z),
#: so date-dependent page logic cannot make two captures differ.
FIXED_EPOCH_S = 1_700_000_000

#: Page time granted before frame 0. Loading consumes virtual time (the clock jumps forward whenever the
#: renderer waits for I/O), so the load is given time in ``LOAD_STEP_MS`` slices until the document is
#: complete and the remainder is granted in one go. Every capture therefore starts at the same page
#: time, which is the point of a synthetic clock.
PREROLL_MS = 2000
LOAD_STEP_MS = 250

#: Real seconds to wait for one ``virtualTimeBudgetExpired`` event before declaring the clock stuck.
EXPIRY_REAL_CAP_S = 10.0

#: ``page.screenshot`` normally takes tens of milliseconds. Measured under a paused virtual clock: it never
#: answers while the document is still loading, and once the document is complete it answered 60/60
#: times on the CSS and GIF pages but hung once at frame 79 of 120 on the rAF page. A picture that has
#: not arrived after this long counts as a failed frame instead of a silent hang.
SCREENSHOT_TIMEOUT_MS = 2000

#: ``beginFrame`` may legitimately return no image for a frame without visual change; retry this many
#: times (a microsecond later each time) before giving up on frame 0, where there is no earlier frame.
BEGIN_FRAME_RETRIES = 4

#: Page clock tolerance: Chromium rounds ``performance.now()`` to 0.1 ms, so two virtual instants that
#: differ by less than that read the same.
CLOCK_TOLERANCE_MS = 0.15

#: Longest the interaction script may run in virtual and in real time (pumped in 16 ms budgets).
INTERACTION_VIRTUAL_CAP_MS = 30_000
INTERACTION_REAL_CAP_S = 60.0
INTERACTION_PUMP_MS = 16

#: Prefix of every note that says pictures cannot come from ``beginFrame`` (tests look for it).
BEGIN_FRAME_UNAVAILABLE = "HeadlessExperimental.beginFrame unavailable"

#: Prefix of the note that says budget-only pictures do not stand still in real time (tests look for it).
BUDGET_MODE_UNUSABLE = "budget-only virtual time is not a verdict-grade picture source on this Chromium"

#: Real milliseconds between the two pictures of a budget-only double-shot (and of the self-test); the
#: same gap strategy A uses (base.py explains the 100 ms).
REAL_GAP_MS = DOUBLE_SHOT_REAL_GAP_MS

#: Self-test page for ``budget_mode_unusable``: one black square whose opacity ramps 0 -> 1 every
#: second (a linear CSS animation, so any passage of rendering time changes its pixels).
PROBE_PAGE_URL = (
    "data:text/html,"
    + quote(
        "<!doctype html><title>FlashTrace virtual-time probe</title>"
        "<style>html,body{margin:0;background:#fff}"
        "div{width:120px;height:120px;background:#000;animation:ft 1s linear infinite}"
        "@keyframes ft{from{opacity:0}to{opacity:1}}</style><div></div>"
    )
)

# Interaction-script helpers, identical in purpose to strategy A's: start the script without awaiting
# it and poll its state while virtual time is pumped forward.
START_INTERACTION_JS = r"""
(source) => {
  const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
  window.__ftInteraction = { state: 'pending', message: '' };
  Promise.resolve()
    .then(() => new AsyncFunction(source)())
    .then(() => { window.__ftInteraction = { state: 'done', message: '' }; },
          (error) => { window.__ftInteraction = { state: 'error', message: String(error) }; });
}
"""
INTERACTION_STATE_EXPRESSION = "JSON.stringify(window.__ftInteraction || { state: 'missing', message: '' })"

# Answers that hold for a whole Chromium build on this platform, remembered so a batch of captures
# (benchmark, ablation) does not re-pay a probe that cannot change its answer. Keyed by browser version;
# both answers are remembered: the reason BeginFrameControl is unavailable, or ``None`` when it works.
_BEGIN_FRAME_PROBED: dict[str, str | None] = {}
_BUDGET_MODE: dict[str, str | None] = {}


def _browser_key(browser: Browser) -> str:
    return f"{browser.browser_type.name} {browser.version}"


def _protocol_message(exc: BaseException) -> str:
    """The first line of a Playwright/DevTools error, without the call log Playwright appends."""
    return str(exc).splitlines()[0].strip() if str(exc) else type(exc).__name__


def _detach_quietly(session: CDPSession | None) -> None:
    """Detach a DevTools session; one whose target has already gone away needs no detaching."""
    if session is None:
        return
    try:
        session.detach()
    except PlaywrightError:
        pass


def _create_begin_frame_target(browser: Browser, context: BrowserContext, scratch: Page, viewport: tuple[int, int]) -> None:
    """``Target.createTarget`` with BeginFrameControl inside ``context`` (raises PlaywrightError when refused).

    Both DevTools sessions it opens are detached again, so a browser shared by many captures does not
    collect one browser-level session per probe.
    """
    page_session: CDPSession | None = None
    browser_session: CDPSession | None = None
    try:
        page_session = context.new_cdp_session(scratch)
        info = page_session.send("Target.getTargetInfo")["targetInfo"]
        browser_session = browser.new_browser_cdp_session()
        browser_session.send(
            "Target.createTarget",
            {
                "url": "about:blank",
                "browserContextId": info.get("browserContextId"),
                "enableBeginFrameControl": True,
                "width": viewport[0],
                "height": viewport[1],
            },
        )
    finally:
        _detach_quietly(browser_session)
        _detach_quietly(page_session)


def begin_frame_unavailable(browser: Browser) -> str | None:
    """Why ``beginFrame`` cannot be used with ``browser`` (``None`` when BeginFrameControl is available).

    The probe is ``Target.createTarget {enableBeginFrameControl: true}`` in a throw-away context: it
    answers immediately with an error on platforms without BeginFrameControl (measured on macOS:
    "BeginFrameControl is not supported on MacOS yet"). Both answers are remembered per Chromium build.
    """
    key = _browser_key(browser)
    if key in _BEGIN_FRAME_PROBED:
        return _BEGIN_FRAME_PROBED[key]
    reason: str | None = None
    context = browser.new_context()
    try:
        _create_begin_frame_target(browser, context, context.new_page(), (320, 240))
    except PlaywrightError as exc:
        reason = f"{BEGIN_FRAME_UNAVAILABLE}: {_protocol_message(exc)}"
    finally:
        context.close()
    _BEGIN_FRAME_PROBED[key] = reason
    return reason


def budget_mode_unusable(browser: Browser) -> str | None:
    """Why budget-only pictures (no BeginFrameControl) cannot carry a verdict with ``browser``; ``None`` if they can.

    Loads ``PROBE_PAGE_URL`` exactly as a capture would, then takes two pictures ``REAL_GAP_MS`` of real
    time apart without advancing virtual time. Identical pictures mean rendering stood still with the
    virtual clock; different pictures mean the compositor draws animations on the real clock, so frame
    rates measured from such pictures would be wrong. Remembered per Chromium build.
    """
    key = _browser_key(browser)
    if key in _BUDGET_MODE:
        return _BUDGET_MODE[key]
    settings = CaptureSettings(url=PROBE_PAGE_URL, duration_s=1, viewport=(200, 200), fps=60, forced=True)
    probe = VirtualTimeStrategy(self_test=True)
    context = browser.new_context(viewport={"width": 200, "height": 200}, device_scale_factor=1)
    try:
        page = context.new_page()
        probe.prepare_page(page, settings)
        reason = probe.navigate(page, settings)
        if reason is None:
            try:
                first = probe._picture(page, 0.0, settings.fps, None)
                page.wait_for_timeout(REAL_GAP_MS)
                second = probe._picture(page, 0.0, settings.fps, first)
            except RuntimeError as exc:
                reason = str(exc)
            else:
                if FrameSink.hash_png(first) != FrameSink.hash_png(second):
                    reason = (
                        f"a CSS opacity animation changed between two pictures taken {REAL_GAP_MS} ms of real time apart "
                        "at one fixed virtual instant: without BeginFrameControl the compositor draws CSS/WAAPI and image "
                        "animations on the real clock while page time stands still, so flash rates would be distorted"
                    )
    finally:
        context.close()
    _BUDGET_MODE[key] = reason
    return reason


class VirtualTimeStrategy(CaptureStrategy):
    """Strategy B. See the module docstring for the algorithm and the measured limits."""

    name: ClassVar[str] = "virtual_time"
    deterministic_by_design: ClassVar[bool] = True
    requires_controllable_content: ClassVar[bool] = False
    launch_args: ClassVar[tuple[str, ...]] = LAUNCH_ARGS

    def __init__(self, self_test: bool = False) -> None:
        self._self_test = self_test        # True for the instance ``budget_mode_unusable`` drives
        self._cdp: CDPSession | None = None
        self._browser_key: str | None = None
        self._begin_frame = False          # True while beginFrame is the picture source
        self._known_nondeterministic: str | None = None  # set when forced to capture in unusable mode
        self._ticks_base: float = 0.0      # renderer TimeTicks (ms) at which virtual time began
        self._virtual_ms: float = 0.0      # virtual ms granted since virtual time began
        self._preroll_ms: float = 0.0      # virtual ms consumed before frame 0 (load + interaction)
        self._budget_expired = False
        self._blocked_reason: str | None = None
        self._setup_notes: list[str] = []

    # ------------------------------------------------------------------ small CDP helpers
    def _send(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if self._cdp is None:
            raise RuntimeError("prepare_page() must run before DevTools commands are sent")
        return self._cdp.send(method, params or {})

    def _evaluate(self, expression: str) -> Any:
        """Evaluate ``expression`` in the page through DevTools; works while the page is paused."""
        result = self._send("Runtime.evaluate", {"expression": expression, "returnByValue": True})
        return result.get("result", {}).get("value")

    def _page_now_ms(self) -> float:
        return float(self._evaluate("performance.now()"))

    def _on_budget_expired(self, _event: dict[str, Any]) -> None:
        self._budget_expired = True

    def _wait_for_budget_expiry(self, page: Page) -> bool:
        """Pump Playwright's event loop until ``virtualTimeBudgetExpired`` arrives (bounded in real time)."""
        deadline = time.monotonic() + EXPIRY_REAL_CAP_S
        while not self._budget_expired and time.monotonic() < deadline:
            page.wait_for_timeout(1)
        return self._budget_expired

    def _advance(self, page: Page, milliseconds: float) -> bool:
        """Let exactly ``milliseconds`` of page time pass, then pause again. False when the clock stuck."""
        if milliseconds <= 0:
            return True
        self._budget_expired = False
        self._send(
            "Emulation.setVirtualTimePolicy",
            {"policy": "advance", "budget": milliseconds, "waitForNavigation": False},
        )
        if not self._wait_for_budget_expiry(page):
            return False
        self._virtual_ms += milliseconds
        return True

    # ------------------------------------------------------------------ setup
    def needs_launch_args(self, browser: Browser) -> bool:
        """Only ask for the dedicated, switch-laden Chromium where BeginFrameControl exists at all."""
        return begin_frame_unavailable(browser) is None

    def describe_setup(self) -> list[str]:
        return list(self._setup_notes)

    def prepare_context(self, context: BrowserContext, settings: CaptureSettings) -> None:
        """Seed ``Math.random`` in every document before any page script runs (ADR-022)."""
        context.add_init_script(SEEDED_RANDOM_JS)
        self._setup_notes.append(SEEDED_RANDOM_NOTE)

    def create_page(self, context: BrowserContext, settings: CaptureSettings) -> Page:
        """Create the capture page with BeginFrameControl when the platform allows it."""
        browser = context.browser
        if browser is None:
            self._setup_notes.append("BeginFrameControl not attempted: the context has no browser handle")
            return context.new_page()
        self._browser_key = _browser_key(browser)
        reason = begin_frame_unavailable(browser)
        if reason is not None:
            self._setup_notes.append(reason + "; using virtual-time budgets with page.screenshot")
            if not self._self_test:
                self._judge_budget_mode(browser, settings)
            return context.new_page()
        page = self._create_begin_frame_page(browser, context, settings)
        return page if page is not None else context.new_page()

    def _judge_budget_mode(self, browser: Browser, settings: CaptureSettings) -> None:
        """Decide whether budget-only pictures may be used: abandon in "auto", capture-but-flag when forced."""
        unusable = budget_mode_unusable(browser)
        if unusable is None:
            self._setup_notes.append("budget-only self-test passed: rendering stood still for 100 ms of real time at a fixed virtual instant")
            return
        note = f"{BUDGET_MODE_UNUSABLE}: {unusable}"
        if settings.forced:
            self._known_nondeterministic = note
            self._setup_notes.append(note + " (forced: capturing anyway, deterministic=false)")
        else:
            self._blocked_reason = note

    def _create_begin_frame_page(self, browser: Browser, context: BrowserContext, settings: CaptureSettings) -> Page | None:
        """The BeginFrameControl page for this capture, or ``None`` (with a note) when it could not be made."""
        scratch = context.new_page()
        new_pages: list[Page] = []

        def remember(new_page: Page) -> None:  # Playwright needs a plain function, not a bound builtin
            new_pages.append(new_page)

        context.on("page", remember)
        try:
            _create_begin_frame_target(browser, context, scratch, settings.viewport)
            deadline = time.monotonic() + 5.0
            while not new_pages and time.monotonic() < deadline:
                scratch.wait_for_timeout(5)
        except PlaywrightError as exc:
            self._setup_notes.append(f"{BEGIN_FRAME_UNAVAILABLE}: {_protocol_message(exc)}; using page.screenshot")
            return None
        finally:
            context.remove_listener("page", remember)
            scratch.close()
        if not new_pages:
            self._setup_notes.append("BeginFrameControl target was created but never attached; using page.screenshot")
            return None
        self._begin_frame = True
        self._setup_notes.append("BeginFrameControl enabled for the capture target (HeadlessExperimental.beginFrame)")
        return new_pages[0]

    def prepare_page(self, page: Page, settings: CaptureSettings) -> None:
        """Attach DevTools and listen for budget expiries; virtual time itself is armed in ``navigate``."""
        self._cdp = page.context.new_cdp_session(page)
        self._cdp.on("Emulation.virtualTimeBudgetExpired", self._on_budget_expired)

    def navigate(self, page: Page, settings: CaptureSettings) -> str | None:
        """Arm virtual time for the coming navigation, load, and grant page time until the document is complete.

        ``waitForNavigation`` defers the policy until the next navigation starts, which keeps the page's
        time origin at virtual zero; it is sent only once the blank page is idle, so that navigation is
        ours. Returns a reason when the document never reaches ``readyState == "complete"`` inside the
        pre-roll, when the clock stops answering, or when pictures are already known to be impossible.
        """
        if self._blocked_reason is not None:
            return self._blocked_reason
        page.wait_for_load_state("load")
        self._budget_expired = False
        result = self._send(
            "Emulation.setVirtualTimePolicy",
            {
                "policy": "pauseIfNetworkFetchesPending",
                "budget": LOAD_STEP_MS,
                "waitForNavigation": True,
                "initialVirtualTime": FIXED_EPOCH_S,
            },
        )
        self._ticks_base = float(result.get("virtualTimeTicksBase", 0.0))
        self._virtual_ms = 0.0
        page.goto(settings.url, wait_until="commit")
        if not self._wait_for_budget_expiry(page):
            return "virtual time never reported its first budget expiry after navigation"
        self._virtual_ms = float(LOAD_STEP_MS)
        while self._evaluate("document.readyState") != "complete":
            if self._virtual_ms >= PREROLL_MS:
                state = self._evaluate("document.readyState")
                return (
                    f"document still at readyState '{state}' after {PREROLL_MS:.0f} ms of virtual time; "
                    "this Chromium does not finish loading under a paused virtual clock"
                )
            if not self._advance(page, LOAD_STEP_MS):
                return "virtual time stopped answering while the document was loading"
        loaded_at = self._virtual_ms
        if not self._advance(page, PREROLL_MS - self._virtual_ms):
            return "virtual time stopped answering during the pre-roll"
        self._preroll_ms = self._virtual_ms
        self._setup_notes.append(
            f"document complete after {loaded_at:.0f} ms of virtual time; frame 0 is at page time {self._preroll_ms:.0f} ms"
        )
        return None

    def wait_for_fonts(self, page: Page) -> list[str]:
        """Read font status through DevTools instead of awaiting a promise the paused page cannot settle."""
        status = self._evaluate("document.fonts.status")
        return [] if status == "loaded" else [f"web fonts were still '{status}' at frame 0 (virtual clock paused)"]

    # ------------------------------------------------------------------ interaction script
    def run_interaction(self, page: Page, script: str) -> list[str]:
        """Run the interaction script while pumping virtual time, exactly as strategy A does."""
        notes: list[str] = []
        page.evaluate(START_INTERACTION_JS, script)
        deadline = time.monotonic() + INTERACTION_REAL_CAP_S
        pumped = 0
        while True:
            state = json.loads(self._evaluate(INTERACTION_STATE_EXPRESSION))
            if state["state"] == "done":
                break
            if state["state"] == "error":
                raise RuntimeError(f"interaction script failed: {state['message']}")
            if state["state"] == "missing":
                notes.append("interaction script navigated away; its completion could not be observed")
                break
            if pumped >= INTERACTION_VIRTUAL_CAP_MS or time.monotonic() > deadline:
                raise TimeoutError(f"interaction script did not finish within {INTERACTION_VIRTUAL_CAP_MS} ms of virtual time")
            if not self._advance(page, INTERACTION_PUMP_MS):
                raise RuntimeError("virtual time stopped answering while the interaction script ran")
            pumped += INTERACTION_PUMP_MS
        self._preroll_ms = self._virtual_ms
        if pumped:
            notes.append(f"interaction script consumed {pumped} ms of virtual time before frame 0")
        return notes

    # ------------------------------------------------------------------ pictures
    def _frame_ticks(self, t_ms: float) -> float:
        """Renderer TimeTicks (ms) that correspond to animation time ``t_ms`` of the capture."""
        return self._ticks_base + self._preroll_ms + t_ms

    def _begin_frame_png(self, ticks: float, fps: int) -> bytes | None:
        """One ``beginFrame`` at ``ticks``; ``None`` when Chromium reports no visual change (no image)."""
        result = self._send(
            "HeadlessExperimental.beginFrame",
            {"frameTimeTicks": ticks, "interval": 1000 / fps, "screenshot": {"format": "png"}},
        )
        data = result.get("screenshotData")
        return base64.b64decode(data) if data else None

    def _picture(self, page: Page, t_ms: float, fps: int, previous: bytes | None) -> bytes:
        """The frame image at animation time ``t_ms`` (raises RuntimeError with the reason when impossible)."""
        if self._begin_frame:
            try:
                for attempt in range(BEGIN_FRAME_RETRIES + 1):
                    # +0.001 ms per retry: frame times must move forward, and a microsecond is invisible.
                    png = self._begin_frame_png(self._frame_ticks(t_ms) + attempt * 0.001, fps)
                    if png is not None:
                        return png
                    if previous is not None:
                        return previous  # no damage since the last frame: identical by definition
                raise RuntimeError(f"beginFrame returned no image for frame at {t_ms:.3f} ms after {BEGIN_FRAME_RETRIES + 1} tries")
            except PlaywrightError as exc:
                # The target accepted BeginFrameControl but the domain is missing (full Chromium build).
                reason = f"{BEGIN_FRAME_UNAVAILABLE}: {_protocol_message(exc)}"
                if self._browser_key is not None:
                    _BEGIN_FRAME_PROBED[self._browser_key] = reason
                self._setup_notes.append(reason + "; switching to page.screenshot")
                self._begin_frame = False
        try:
            return page.screenshot(
                type="png", animations="allow", caret="hide", full_page=False, scale="css", timeout=SCREENSHOT_TIMEOUT_MS
            )
        except PlaywrightTimeoutError as exc:
            raise RuntimeError(
                f"page.screenshot did not return within {SCREENSHOT_TIMEOUT_MS} ms while virtual time was paused "
                "(the renderer produced no frame; BeginFrameControl is unavailable)"
            ) from exc

    def _double_shot_matches(self, page: Page, t_ms: float, fps: int, reference: bytes, reference_hash: str) -> bool:
        """Second picture at the same virtual time; identical pixels mean nothing moved on its own.

        In budget-only mode the second picture waits ``REAL_GAP_MS`` of real time first (measured: a
        120 ms GIF slipped through two back-to-back pictures 4 times out of 6).
        """
        if not self._begin_frame:
            page.wait_for_timeout(REAL_GAP_MS)
        second = self._picture(page, t_ms, fps, reference)
        return FrameSink.hash_png(second) == reference_hash

    # ------------------------------------------------------------------ frames
    def run(
        self,
        page: Page,
        settings: CaptureSettings,
        sink: FrameSink,
        progress: ProgressCallback | None,
    ) -> StrategyOutcome:
        total = settings.frame_count
        failures: list[str] = []
        deterministic = True
        previous: bytes | None = None
        clock_at_frame0: float | None = None

        for index in range(total):
            t_ms = settings.frame_time_ms(index)
            if index > 0 and not self._advance(page, t_ms - settings.frame_time_ms(index - 1)):
                failures.append(f"virtual time stopped answering before frame {index}")
                break
            try:
                png = self._picture(page, t_ms, settings.fps, previous)
            except RuntimeError as exc:
                failures.append(f"frame {index}: {exc}")
                break
            previous = png
            digest = sink.add(png)
            if index in (0, total - 1):
                deterministic &= self._check_frame(page, settings, index, t_ms, png, digest, failures)
                now = self._page_now_ms()
                clock_at_frame0 = now if clock_at_frame0 is None else clock_at_frame0
                expected = clock_at_frame0 + t_ms
                if abs(now - expected) > CLOCK_TOLERANCE_MS:
                    deterministic = False
                    failures.append(f"page clock read {now:.1f} ms at frame {index}, expected {expected:.1f} ms")
            if progress is not None:
                progress(index + 1, total)

        if len(sink.hashes) < total:
            failures.append(f"only {len(sink.hashes)} of {total} frames were captured")
        if self._known_nondeterministic is not None:
            deterministic = False
            failures.append(self._known_nondeterministic)
        notes = list(self._setup_notes)
        if clock_at_frame0 is not None:
            notes.append(f"page clock read {clock_at_frame0:.1f} ms at frame 0 (virtual time; Date.now() starts at {FIXED_EPOCH_S})")
        notes.append(
            "pictures via HeadlessExperimental.beginFrame" if self._begin_frame else "pictures via page.screenshot between virtual-time budgets"
        )
        failure_reason = "; ".join(failures) if failures else None
        return StrategyOutcome(deterministic=deterministic and not failures, notes=notes + failures, failure_reason=failure_reason)

    def _check_frame(
        self,
        page: Page,
        settings: CaptureSettings,
        index: int,
        t_ms: float,
        png: bytes,
        digest: str,
        failures: list[str],
    ) -> bool:
        """Double-shot check for frame ``index``; appends the failure text and returns False on a mismatch."""
        try:
            if self._double_shot_matches(page, t_ms, settings.fps, png, digest):
                return True
        except RuntimeError as exc:
            failures.append(f"double-shot check at frame {index} could not take its second picture: {exc}")
            return False
        failures.append(f"double-shot check failed at frame {index}: two pictures at the same virtual time differ")
        return False
