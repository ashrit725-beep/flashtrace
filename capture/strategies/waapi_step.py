"""Strategy A ``waapi_step``: deterministic frame capture by seeking (SPEC.md section 6.2).

How it works, in plain words:

1. Before the page loads, Playwright's fake clock is installed and paused at virtual time 0. From
   then on ``Date``, ``setTimeout``/``setInterval``, ``requestAnimationFrame`` and
   ``performance.now()`` inside the page only move when we say so. JavaScript-driven animations
   (rAF loops, timers) are therefore frozen between frames.
2. CSS animations, CSS transitions and Web Animations API animations run on the browser's own
   animation clock, which the fake clock does not touch. Every one of them (in the main frame and all
   same-origin frames) is paused through the Web Animations API and seeked explicitly before each
   screenshot: ``currentTime = t_i - startOffset``.
3. ``<video>`` elements are paused, muted and seeked to ``t_i`` per frame.
4. Per frame ``i`` (``t_i = i * 1000 / fps`` ms): advance the fake clock to ``round(t_i)`` (one
   ``run_for`` call, so timers and rAF callbacks fire), seek every animation and video (one
   ``evaluate`` call), force a style flush, screenshot. That is one clock call, one evaluate and one
   screenshot per frame.
5. Double-shot check at frame 0 and the last frame: two screenshots at the same virtual time must be
   identical, otherwise something animates on a clock we do not control and the capture is marked
   ``deterministic=False``.

Two facts about Playwright 1.63's fake clock shaped the implementation (see ADR notes in the
orchestrator's record):

* The clock only steps in whole milliseconds (``_runTo`` rounds ticks up). Frames at 60 fps are
  16.667 ms apart, so the clock is stepped by ``round(t_i) - round(t_{i-1})`` (16 or 17 ms) and the
  animation seeks use the exact ``t_i``. The two time bases never drift apart by more than 0.5 ms.
* A new document replays the clock's command log and advances its tick counter by the real
  milliseconds that passed between our ``install`` and ``pause_at`` calls. That offset changes from
  run to run, and because the fake ``requestAnimationFrame`` fires on multiples of 16 ticks it would
  move rAF-driven state by a frame between two identical captures. ``CLOCK_REBASE_JS`` therefore
  pins the tick counter at document start, so ``performance.now()`` reads exactly 0 (or exactly the
  virtual time so far, for documents created later).
"""

from __future__ import annotations

import time
from typing import Any, ClassVar

from playwright.sync_api import BrowserContext, Page

from capture.strategies.base import (
    CaptureSettings,
    CaptureStrategy,
    FrameSink,
    ProgressCallback,
    StrategyOutcome,
    double_shot_matches,
    take_screenshot,
)

# Longest the interaction script may take in *virtual* time before we give up (it is pumped in
# 16 ms steps, see ``run_interaction``), and in real time.
INTERACTION_VIRTUAL_CAP_MS = 30_000
INTERACTION_REAL_CAP_S = 60.0
INTERACTION_PUMP_MS = 16

# Init script, registered after ``clock.install``/``clock.pause_at`` so it runs after Playwright's own
# clock scripts and before any page script. It touches two private fields of the injected clock
# (``_log`` and ``_now``); if a future Playwright renames them the script records 'unavailable' and the
# strategy marks the capture non-deterministic rather than silently producing drifting frames.
CLOCK_REBASE_JS = r"""
(() => {
  const controller = globalThis.__pwClock && globalThis.__pwClock.controller;
  if (!controller || !Array.isArray(controller._log) || !controller._now) {
    globalThis.__ftClockRebase = 'unavailable';
    return;
  }
  // Virtual time this document should start at: the sum of every clock step issued so far.
  // For the first document that is 0; for a document created at frame j it is round(t_j).
  let expectedTicks = 0;
  for (const entry of controller._log) {
    if (entry.type === 'runFor' || entry.type === 'fastForward') expectedTicks += entry.param;
  }
  controller.performanceNow();            // forces the log replay now, while no page script has run
  controller._now.ticks = expectedTicks;  // tick counter: performance.now(), rAF scheduling
  controller._now.time = expectedTicks;   // wall time: Date.now(); the clock was installed at epoch 0
  globalThis.__ftClockRebase = 'ok';
})();
"""

# Per-frame page-side helper. Walks the main window and every same-origin frame, registers animations
# it has not seen before (pausing them and remembering when they first appeared), seeks everything
# to the frame time, seeks <video> elements, then forces a style/layout flush so the screenshot
# shows the seeked state. Returns counters for the capture notes.
SEEK_FRAME_JS = r"""
async ({ tMs, isFirst }) => {
  const W = window;
  const registry = W.__ftRegistry || (W.__ftRegistry = { nextId: 1, offsets: new Map(), videos: new WeakSet() });
  const stats = { animations: 0, newAnimations: 0, pagePaused: 0, unseekable: 0,
                  videos: 0, videosNotReady: 0, videoSeeks: 0, videoSeekTimeouts: 0, videoWaitSkipped: 0,
                  perfNow: 0, rebase: W.__ftClockRebase || 'missing' };

  // Same-origin frames only: touching a cross-origin frame's document throws, and detect.py already
  // reported such frames as uncontrollable.
  const windows = [];
  const visit = (win) => {
    windows.push(win);
    for (let i = 0; i < win.frames.length; i++) {
      const child = win.frames[i];
      try { if (child.document) visit(child); } catch (e) { /* cross-origin */ }
    }
  };
  visit(W);

  // Pass 1: find every animation and register the ones we have not seen before.
  const found = [];  // animations to seek this frame
  const fresh = [];  // animations that must be paused first (new, or resumed by the page)
  for (const win of windows) {
    let animations;
    try { animations = win.document.getAnimations(); } catch (e) { continue; }
    for (const animation of animations) {
      stats.animations++;
      if (animation.__ftId === undefined) {
        // First sighting. SPEC 6.2: animations present at frame 0 start at 0; animations discovered
        // later start at the frame in which they first appear.
        animation.__ftId = registry.nextId++;
        registry.offsets.set(animation.__ftId, isFirst ? 0 : tMs);
        if (!isFirst) stats.newAnimations++;
        // An animation the page itself holds paused must stay still, not be driven by us.
        animation.__ftPagePaused = animation.playState === 'paused';
        if (!animation.__ftPagePaused) fresh.push(animation);
      } else if (animation.__ftPagePaused && animation.playState !== 'paused') {
        // The page resumed an animation it had paused: treat it as starting now.
        animation.__ftPagePaused = false;
        registry.offsets.set(animation.__ftId, tMs);
        fresh.push(animation);
      }
      if (animation.__ftPagePaused) { stats.pagePaused++; continue; }
      found.push(animation);
    }
  }

  // The page's setTimeout is fake and frozen, so any real-time timeout must use the browser's original
  // timer, which Playwright keeps at __pwClock.builtins.
  const builtins = W.__pwClock && W.__pwClock.builtins;
  const realSetTimeout = builtins && typeof builtins.setTimeout === 'function' ? builtins.setTimeout : null;
  const withTimeout = (promise, ms) => realSetTimeout
    ? Promise.race([promise, new Promise((resolve) => realSetTimeout(() => resolve('timeout'), ms))])
    : promise;
  const allReady = (list) => withTimeout(Promise.all(list.map((a) => a.ready.catch(() => undefined))), 500);

  // Pass 2: pause fresh animations with a handshake. Measured on Chromium 1243: pausing an animation
  // that is still starting ("play-pending") and seeking it in the same task leaves the compositor one
  // frame ahead of the seeked time (an opacity fader showed 1/60 s of progress at t=0, and the amount
  // varied between runs). Letting the animation start (await ready), pausing, waiting for the pause to
  // be applied (await ready again) and only then seeking gives the exact seeked state every time.
  if (fresh.length) {
    await allReady(fresh);
    for (const animation of fresh) { try { animation.pause(); } catch (e) {} }
    await allReady(fresh);
  }

  // Pass 3: seek every controlled animation to the frame time.
  for (const animation of found) {
    const local = Math.max(0, tMs - registry.offsets.get(animation.__ftId));
    const rate = typeof animation.playbackRate === 'number' ? animation.playbackRate : 1;
    let target = rate * local;
    if (rate < 0) {
      // A reversed animation plays from its end towards 0.
      let end = Infinity;
      try { end = animation.effect.getComputedTiming().endTime; } catch (e) {}
      target = Number.isFinite(end) ? Math.max(0, end + rate * local) : 0;
    }
    try { animation.currentTime = target; } catch (e) { stats.unseekable++; }
  }

  // Pass 4: videos, then a style flush per window.
  const pendingSeeks = [];
  for (const win of windows) {
    for (const video of win.document.querySelectorAll('video')) {
      stats.videos++;
      if (!registry.videos.has(video)) {
        registry.videos.add(video);
        try { video.pause(); video.muted = true; video.autoplay = false; } catch (e) {}
      }
      // readyState 1 = HAVE_METADATA: duration is known and seeking is possible.
      if (!(video.readyState >= 1) || !Number.isFinite(video.duration) || video.duration <= 0) {
        stats.videosNotReady++;
        continue;
      }
      let target = tMs / 1000;
      target = video.loop ? target % video.duration : Math.min(target, video.duration);
      if (Math.abs(video.currentTime - target) < 1e-4) continue;   // 'seeked' never fires for a no-op
      stats.videoSeeks++;
      pendingSeeks.push(new Promise((resolve) => {
        const done = () => { video.removeEventListener('seeked', done); resolve(true); };
        video.addEventListener('seeked', done);
        video.currentTime = target;
      }));
    }

    // Reading offsetHeight forces style recalculation and layout, so the seeked animation values are
    // applied before the screenshot rather than at some later browser-chosen moment.
    try { void win.document.documentElement.offsetHeight; } catch (e) {}
  }
  stats.perfNow = W.performance.now();

  if (pendingSeeks.length === 0) return stats;
  if (!realSetTimeout) {
    // Without a real timer we cannot bound the wait for 'seeked', so do not wait at all.
    stats.videoWaitSkipped = pendingSeeks.length;
    return stats;
  }
  const outcome = await withTimeout(Promise.all(pendingSeeks).then(() => 'ok'), 2000);
  if (outcome === 'timeout') stats.videoSeekTimeouts = pendingSeeks.length;
  return stats;
}
"""

# Starts the interaction script without waiting for it and records its state on window, so the
# strategy can pump virtual time until it settles (see ``run_interaction``).
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

INTERACTION_STATE_JS = "() => window.__ftInteraction || { state: 'missing', message: '' }"


class WaapiStepStrategy(CaptureStrategy):
    """Strategy A. See the module docstring for the algorithm."""

    name: ClassVar[str] = "waapi_step"
    deterministic_by_design: ClassVar[bool] = True
    requires_controllable_content: ClassVar[bool] = True

    def __init__(self) -> None:
        # Virtual milliseconds already consumed before frame 0 (by the interaction script).
        self._preroll_ms = 0
        self._context: BrowserContext | None = None

    # ------------------------------------------------------------------ setup
    def prepare_context(self, context: BrowserContext, settings: CaptureSettings) -> None:
        """Install and pause the fake clock at virtual 0 before any page exists.

        Doing this on the context (no pages yet) means every document, including the first one,
        starts with the clock already paused; no real time can leak into page-visible time.
        """
        self._context = context
        context.clock.install(time=0)
        context.clock.pause_at(0)
        context.add_init_script(CLOCK_REBASE_JS)

    def _advance_clock(self, milliseconds: int) -> None:
        """Move virtual time forward, firing every timer and rAF callback that falls due."""
        if self._context is None:
            raise RuntimeError("prepare_context() must run before the clock is advanced")
        self._context.clock.run_for(int(milliseconds))

    # ------------------------------------------------------------------ interaction script
    def run_interaction(self, page: Page, script: str) -> list[str]:
        """Run the interaction script, pumping virtual time until its promise settles.

        Page timers are frozen, so ``await new Promise(r => setTimeout(r, 500))`` inside the script
        would hang forever if we simply awaited it. Instead the script is started, and while it is
        still pending the clock is advanced in 16 ms steps. The virtual time consumed this way is
        remembered as the pre-roll: frame 0's JavaScript clock then reads ``preroll_ms``.
        """
        notes: list[str] = []
        page.evaluate(START_INTERACTION_JS, script)
        deadline = time.monotonic() + INTERACTION_REAL_CAP_S
        pumped = 0
        while True:
            state: dict[str, Any] = page.evaluate(INTERACTION_STATE_JS)
            if state["state"] == "done":
                break
            if state["state"] == "error":
                raise RuntimeError(f"interaction script failed: {state['message']}")
            if state["state"] == "missing":
                notes.append("interaction script navigated away; its completion could not be observed")
                break
            if pumped >= INTERACTION_VIRTUAL_CAP_MS or time.monotonic() > deadline:
                raise TimeoutError(
                    f"interaction script did not finish within {INTERACTION_VIRTUAL_CAP_MS} ms of virtual time"
                )
            self._advance_clock(INTERACTION_PUMP_MS)
            pumped += INTERACTION_PUMP_MS
        self._preroll_ms = pumped
        if pumped:
            notes.append(f"interaction script consumed {pumped} ms of virtual time before frame 0")
        return notes

    # ------------------------------------------------------------------ frames
    def run(
        self,
        page: Page,
        settings: CaptureSettings,
        sink: FrameSink,
        progress: ProgressCallback | None,
    ) -> StrategyOutcome:
        total = settings.frame_count
        notes: list[str] = []
        deterministic = True
        failures: list[str] = []
        clock_ms = 0  # virtual ms advanced since frame-loop start (excludes the pre-roll)
        first_stats: dict[str, Any] | None = None
        late_animations = 0
        unseekable = 0
        video_seek_timeouts = 0
        video_wait_skipped = 0

        for index in range(total):
            t_ms = settings.frame_time_ms(index)
            target_ms = int(round(t_ms))
            # Frame 0 still issues run_for(0): it fires timers already due at virtual 0 so the page
            # is in the same state whether or not a due timer happened to fire during setup.
            self._advance_clock(target_ms - clock_ms)
            clock_ms = target_ms

            stats: dict[str, Any] = page.evaluate(SEEK_FRAME_JS, {"tMs": t_ms, "isFirst": index == 0})
            late_animations += int(stats["newAnimations"])
            unseekable += int(stats["unseekable"])
            video_seek_timeouts += int(stats["videoSeekTimeouts"])
            video_wait_skipped += int(stats["videoWaitSkipped"])
            if first_stats is None:
                first_stats = stats

            # The page's JavaScript clock must read exactly the virtual time we set. Anything else
            # means real time leaked in, and rAF-driven pages would not reproduce.
            expected_now = self._preroll_ms + target_ms
            if stats["perfNow"] != expected_now and index in (0, total - 1):
                deterministic = False
                failures.append(
                    f"page clock read {stats['perfNow']} ms at frame {index}, expected {expected_now} ms"
                )

            png = take_screenshot(page)
            digest = sink.add(png)
            if index in (0, total - 1) and not double_shot_matches(page, digest):
                deterministic = False
                failures.append(f"double-shot check failed at frame {index}: two screenshots at the same virtual time differ")
            if progress is not None:
                progress(index + 1, total)

        if first_stats is not None:
            notes.append(f"{first_stats['animations']} animation(s) found at frame 0, {late_animations} discovered later")
            if first_stats["rebase"] != "ok":
                deterministic = False
                failures.append(f"fake clock re-base unavailable ({first_stats['rebase']}); page time origin may vary")
            if first_stats["pagePaused"]:
                notes.append(f"{first_stats['pagePaused']} animation(s) were already paused by the page and were left paused")
            if first_stats["videos"]:
                notes.append(f"{first_stats['videos']} video element(s) paused, muted and seeked per frame")
            if first_stats["videosNotReady"]:
                notes.append(f"{first_stats['videosNotReady']} video element(s) had no metadata and could not be seeked")
        if unseekable:
            notes.append(f"{unseekable} animation seek(s) were rejected by the browser (for example scroll-driven timelines)")
        if video_seek_timeouts:
            notes.append(f"{video_seek_timeouts} video seek(s) did not report 'seeked' within 2 s")
        if video_wait_skipped:
            notes.append(f"{video_wait_skipped} video seek(s) could not be awaited (no original timer available)")
        notes.append(f"fake clock stepped in whole ms: frame i sits at round(i*1000/{settings.fps}) ms, animations seeked to the exact time")

        failure_reason = "; ".join(failures) if failures else None
        return StrategyOutcome(deterministic=deterministic, notes=notes + failures, failure_reason=failure_reason)
