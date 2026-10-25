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
from typing import ClassVar

from playwright.sync_api import Browser, BrowserContext, Page

from capture.frames import FrameSequence, png_to_rgb
from capture.hashing import frame_hash

# progress(frames_done, frames_total); called once per stored frame.
ProgressCallback = Callable[[int, int], None]

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
        """
        page.goto(settings.url, wait_until="load")
        return None

    def wait_for_fonts(self, page: Page) -> list[str]:
        """Block until web fonts are ready, so text pixels do not change mid-capture; returns notes.

        Strategies that freeze the page's task queues override this, because awaiting
        ``document.fonts.ready`` on a frozen page would never return.
        """
        page.evaluate("() => document.fonts.ready.then(() => undefined)")
        return []

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
