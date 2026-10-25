"""Pieces shared by every capture strategy (SPEC.md section 6.2).

A *strategy* is one way of turning a live page into frames at fixed animation times. Strategy A
(``waapi_step``) seeks animations and a fake clock; B (``virtual_time``) uses Chromium virtual time
through the DevTools protocol; C (``realtime``) records a screencast and resamples it. They differ in
how time is controlled and how pictures are taken, so the browser setup, frame storage, hashing and
the fall-through live here and in ``capturer.py``; each strategy implements the small
``CaptureStrategy`` interface below and may override the page-creation, navigation and font-wait hooks
when its time source needs them.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar

from playwright.sync_api import Browser, BrowserContext, Page
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from capture.frames import FrameSequence, png_to_rgb
from capture.hashing import frame_hash

if TYPE_CHECKING:  # type hints only: detect.py does not import the strategies
    from capture.detect import ContentWatch

# progress(frames_done, frames_total); called once per stored frame.
ProgressCallback = Callable[[int, int], None]

#: Real milliseconds the page's ``load`` event may take once its HTML has arrived. One subresource that
#: never answers (a stalled tracking pixel, a font host that hangs) holds ``load`` back for ever, so after
#: this long the capture goes on without it and says so. What that request could still change is then
#: the strategy's business: strategy A's network wait gives up on it and marks the capture
#: ``deterministic=False`` (``network_wait.py``); strategy C is never deterministic.
LOAD_WAIT_MS = 10_000

#: Real milliseconds to wait for ``document.fonts.ready`` before frame 0. A font whose server never
#: answers would otherwise block the capture for ever (no timeout applies to ``page.evaluate``). As long
#: as the load event's, so a slow but working font host still gets a deterministic capture.
FONT_WAIT_MS = 10_000

# Settles with 'loaded' once every web font is ready, or with document.fonts.status after limitMs of real
# time. Strategy A's setTimeout is the frozen fake clock, so the browser's original timer, which
# Playwright keeps at __pwClock.builtins, bounds the wait there.
WAIT_FOR_FONTS_JS = r"""
(limitMs) => {
  const builtins = window.__pwClock && window.__pwClock.builtins;
  const realSetTimeout = builtins && typeof builtins.setTimeout === 'function' ? builtins.setTimeout : setTimeout;
  return Promise.race([
    document.fonts.ready.then(() => 'loaded'),
    new Promise((resolve) => realSetTimeout(() => resolve(document.fonts.status), limitMs)),
  ]);
}
"""


class PageLoadError(Exception):
    """The page cannot be audited at all, whichever strategy loads it (for example its server answered
    with an HTTP error status). ``capturer`` does not fall through on it: every strategy would load the
    same answer. The audit layer reports it as a capture error (SPEC 6.4)."""


def check_main_response(response: Any, url: str) -> None:
    """Raise ``PageLoadError`` when the main document came back with an HTTP error status (>= 400).

    Without this check a mistyped URL or a broken deploy is audited as the server's error page and can
    PASS. ``response`` is what ``page.goto`` returned; it is ``None`` for ``data:`` and ``about:`` URLs,
    and a ``file:`` URL has no HTTP status to check.
    """
    status = getattr(response, "status", None)
    if isinstance(status, int) and status >= 400:
        text = str(getattr(response, "status_text", "") or "").strip()
        raise PageLoadError(f"the page answered HTTP {status}{' ' + text if text else ''}: {url}")


def wait_for_load_event(page: Page) -> list[str]:
    """Wait at most ``LOAD_WAIT_MS`` of real time for the ``load`` event; a note when it did not come."""
    try:
        page.wait_for_load_state("load", timeout=LOAD_WAIT_MS)
    except PlaywrightTimeoutError:
        return [
            f"the page's load event had not fired after {LOAD_WAIT_MS / 1000:g} s of real time "
            "(a request it waits for never finished); the capture went on without it"
        ]
    return []

#: Real milliseconds between the two screenshots of the double-shot check (SPEC 6.2, ADR-022). The
#: virtual clocks stand still during the wait, so only content that animates on the real clock can
#: change. 100 ms is longer than one frame of any animation faster than 10 Hz, so such content changes
#: between the two pictures instead of slipping through by luck.
DOUBLE_SHOT_REAL_GAP_MS = 100

# Where inside its 1/fps time slot each frame is sampled (ADR-029): a quarter of the way in.
SAMPLE_PHASE = 0.25


@dataclass(frozen=True)
class CaptureSettings:
    """Everything a strategy needs to know about the requested capture."""

    url: str
    duration_s: float
    viewport: tuple[int, int]
    fps: int
    interaction_script: str | None = None
    extra_css: str | None = None
    #: True when the caller named this strategy explicitly (no fall-through will follow), so a strategy
    #: that knows it cannot promise determinism may still capture for inspection instead of giving up.
    forced: bool = False
    #: Whether local files may be read: the page itself and, for a local page, its images (detect.py).
    allow_local_files: bool = True
    #: Ablation (ADR-013): CSS selectors of the elements whose animations must not be driven.
    suppress_selectors: tuple[str, ...] = ()

    @property
    def frame_count(self) -> int:
        """Number of frames: duration times fps, rounded to the nearest whole frame."""
        return int(round(self.duration_s * self.fps))

    def frame_time_ms(self, index: int) -> float:
        """Animation time of frame ``index`` in milliseconds: a quarter of the way into its time slot.

        Frame ``i`` stands for the slot from ``i / fps`` to ``(i + 1) / fps`` seconds and shows the page at
        ``(i + 1/4) / fps`` (SPEC 5.1, ADR-029). Sampling exactly at ``i / fps`` put many sample times on
        the exact instant a CSS animation switches state (200 ms = 12 frames); Chromium then shows the old
        state at some of those instants and the new one at others (0.6 / 0.2 = 2.9999999999999996), which
        moved some transitions a frame late and made a 5 Hz strobe measure 6 per second. A quarter-frame
        offset is never a whole millisecond or a third of one at 60 fps, so it never lands on such a switch.
        """
        return (index + SAMPLE_PHASE) * 1000 / self.fps


@dataclass
class StrategyOutcome:
    """What a strategy reports back after producing its frames."""

    deterministic: bool
    notes: list[str] = field(default_factory=list)
    # Set when the capture should fall through to the next strategy (for example a failed
    # double-shot check); ``None`` means the frames are good.
    failure_reason: str | None = None
    # Uncontrollable content the strategy found during the frame loop (it appeared after frame 0),
    # in the same form as ``detect.find_uncontrollable``'s entries; added to ``meta.uncontrollable``.
    uncontrollable: list[str] = field(default_factory=list)


class FrameSink:
    """Receives screenshots, stores them as frames and keeps the per-frame hashes."""

    def __init__(self, frames: FrameSequence) -> None:
        self.frames = frames
        self.hashes: list[str] = []

    @staticmethod
    def hash_png(png_bytes: bytes) -> str:
        """Hash of the *decoded* pixels of a PNG (SPEC 6.1)."""
        return frame_hash(png_to_rgb(png_bytes))

    def add(self, png_bytes: bytes) -> str:
        """Store one screenshot as the next frame and return its hash."""
        digest = self.hash_png(png_bytes)
        self.frames.append_png(png_bytes)
        self.hashes.append(digest)
        return digest


def take_screenshot(page: Page) -> bytes:
    """Screenshot the viewport exactly as it is.

    ``animations="allow"`` tells Playwright not to touch CSS animations (its "disabled" mode would
    fast-forward them, destroying the state we seeked). The caret is hidden because a blinking text
    cursor would be a tiny flashing region. ``scale="css"`` gives one image pixel per CSS pixel.
    """
    return page.screenshot(type="png", animations="allow", caret="hide", full_page=False, scale="css")


def double_shot_matches(page: Page, reference_hash: str) -> bool:
    """Wait ``DOUBLE_SHOT_REAL_GAP_MS`` of real time, take a second screenshot and report whether it is
    pixel-identical to ``reference_hash``.

    SPEC 6.2 double-shot check: nothing on the page may change between two screenshots taken at the
    same virtual time. If something does, it is animating on a clock the strategy does not control.
    ``page.wait_for_timeout`` waits in the Playwright driver, not with the page's (frozen) timers.
    """
    page.wait_for_timeout(DOUBLE_SHOT_REAL_GAP_MS)
    return FrameSink.hash_png(take_screenshot(page)) == reference_hash


# The interaction script is user-supplied JS source. Running it as the body of an async function
# lets it use ``await`` and ``return`` naturally, and a plain statement list works too.
RUN_INTERACTION_JS = r"""
(source) => {
  const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
  return new AsyncFunction(source)();
}
"""


class CaptureStrategy(ABC):
    """Interface every strategy implements. Registered by name in ``capture.strategies.REGISTRY``."""

    #: Value written to ``meta.strategy`` (must match the report schema enum).
    name: ClassVar[str]
    #: False for strategies that can never promise identical frames (realtime screencast).
    deterministic_by_design: ClassVar[bool] = True
    #: True when the strategy must be skipped in "auto" mode if ``detect.find_uncontrollable`` finds
    #: content it cannot drive (animated images, WebGL, cross-origin iframes).
    requires_controllable_content: ClassVar[bool] = False
    #: Extra Chromium command-line switches the strategy needs. When non-empty, ``capturer`` launches a
    #: dedicated browser with them for this attempt and leaves the caller's browser untouched.
    launch_args: ClassVar[tuple[str, ...]] = ()
    #: True when the strategy re-applies an ablation's suppression on every frame (strategy A). The
    #: others apply it once, before frame 0 (``capturer``), which the capture notes say.
    reapplies_suppression: ClassVar[bool] = False
    #: True when the strategy's pictures come from ``page.screenshot``, which waits for
    #: ``document.fonts.ready`` before every picture (Playwright 1.63, ``Screenshotter._preparePageForScreenshot``).
    #: Such a strategy cannot capture a page whose web font is still loading after ``FONT_WAIT_MS``: each
    #: picture would wait for its 30 s timeout. ``capturer`` then gives the attempt up.
    pictures_wait_for_fonts: ClassVar[bool] = False

    def __init__(self) -> None:
        #: Notes about how the page loaded (for example a ``load`` event that never came), which
        #: ``capturer`` copies into the capture notes after navigation.
        self.load_notes: list[str] = []
        #: Set by ``capturer`` after its scan for uncontrollable content, before ``run``: the scan's state,
        #: so a strategy can look again for content that appears after frame 0 (strategy A does).
        self.content_watch: ContentWatch | None = None

    def needs_controllable_content(self) -> bool:
        """Whether this attempt must step aside for uncontrollable content (``requires_controllable_content``
        unless the strategy knows better once its page exists; strategy B decides by its picture source)."""
        return self.requires_controllable_content

    def needs_launch_args(self, browser: Browser) -> bool:
        """Whether this attempt should run in a dedicated browser launched with ``launch_args``.

        ``browser`` is the caller's browser; a strategy can probe it (for example for a DevTools
        feature) and decline the dedicated launch when the switches would not help on this platform.
        """
        return bool(self.launch_args)

    def describe_setup(self) -> list[str]:
        """Notes about the strategy's setup, used when the page never loaded and ``run`` never ran."""
        return []

    def prepare_context(self, context: BrowserContext, settings: CaptureSettings) -> None:
        """Hook run on the fresh browser context before any page exists (install clocks here)."""

    def create_page(self, context: BrowserContext, settings: CaptureSettings) -> Page:
        """Create the page that will be captured (override to create it through the DevTools protocol)."""
        return context.new_page()

    def prepare_page(self, page: Page, settings: CaptureSettings) -> None:
        """Hook run on the new page before navigation."""

    def navigate(self, page: Page, settings: CaptureSettings) -> str | None:
        """Load ``settings.url``; return ``None`` on success or a reason the page could not be loaded.

        A returned reason means the strategy cannot capture this page at all (for example its time
        source stalled the load). In "auto" mode the capturer then moves on to the next strategy.
        An HTTP error status of the page itself raises ``PageLoadError`` instead (no strategy can help).

        The navigation itself waits for the HTML (``domcontentloaded``); the ``load`` event, which also
        waits for every image, font and frame, is then given ``LOAD_WAIT_MS`` of real time.
        """
        response = page.goto(settings.url, wait_until="domcontentloaded")
        check_main_response(response, settings.url)
        self.load_notes.extend(wait_for_load_event(page))
        return None

    def wait_for_fonts(self, page: Page) -> str | None:
        """Wait, at most ``FONT_WAIT_MS`` of real time, until web fonts are ready, so text pixels do not
        change mid-capture. Returns ``None`` when they are, else a note saying they were not.

        A font still loading after that is a request that has not finished; its text may change when it
        arrives, at a moment real time decides. ``capturer`` records the note and gives up an attempt whose
        pictures wait for fonts (``pictures_wait_for_fonts``); strategy C is never deterministic anyway.
        Strategies that freeze the page's task queues override this, because awaiting
        ``document.fonts.ready`` on a frozen page would never return.
        """
        status = page.evaluate(WAIT_FOR_FONTS_JS, FONT_WAIT_MS)
        if status == "loaded":
            return None
        return f"web fonts were still '{status}' after {FONT_WAIT_MS / 1000:g} s of real time; text drawn in them may change when they arrive"

    def run_interaction(self, page: Page, script: str) -> list[str]:
        """Run the interaction script after load and before frame 0; returns notes.

        The default waits for the script's promise in real time. Strategies that freeze page timers
        override this, because a script that awaits ``setTimeout`` would otherwise never finish.
        """
        page.evaluate(RUN_INTERACTION_JS, script)
        return []

    @abstractmethod
    def run(
        self,
        page: Page,
        settings: CaptureSettings,
        sink: FrameSink,
        progress: ProgressCallback | None,
    ) -> StrategyOutcome:
        """Produce ``settings.frame_count`` frames into ``sink`` and describe the result."""
