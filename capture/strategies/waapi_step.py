"""Strategy A ``waapi_step``: deterministic frame capture by seeking (SPEC.md section 6.2).

How it works, in plain words:

1. Before the page loads, Playwright's fake clock is installed and paused at virtual time 0. From
   then on ``Date``, ``setTimeout``/``setInterval``, ``requestAnimationFrame``, ``performance.now()``
   and (through ``TIMELINE_CLOCK_JS``) ``document.timeline.currentTime`` inside the page only move when
   we say so. JavaScript-driven animations (rAF loops, timers) are therefore frozen between frames.
2. CSS animations, CSS transitions and Web Animations API animations run on the browser's own
   animation clock, which the fake clock does not touch. Every one of them (in the document, in every
   shadow root, open or closed, and in every same-origin frame; ADR-022) is paused through the Web
   Animations API and seeked explicitly before each screenshot:
   ``currentTime = phase + rate * (t - startOffset)``, where ``phase`` is the current time the page
   itself gave the animation (0 when it gave none). An animation the page sets running again after we
   paused it is paused and seeked again (re-adopted).
3. What the page asks of its animations is honoured (``ANIMATION_RECORDER_JS`` records it): an
   animation the page pauses, with ``pause()`` or CSS ``animation-play-state``, is held still and
   continues from there when the page resumes it; a current time the page assigns restarts the
   animation's clock from that time. When an animation reaches its end it is finished through the API
   (a paused animation never finishes by itself), so the page's ``finished`` promise callbacks and
   ``finish`` handlers run, and an animation they start is driven from that frame.
4. ``<video>`` elements are paused, muted and seeked per frame. SVG SMIL timelines (one per outermost
   ``<svg>``) are paused with ``pauseAnimations()`` and set with ``setCurrentTime``; one the page
   paused itself stands still.
5. Real-time work is allowed to finish while virtual time stands still: the page's network requests
   (``network_wait.py``), and every ``<video>``/``<img>`` in the viewport that is still loading. In
   virtual time they therefore arrive at the frame in which the page asked for them. A wait that runs
   out marks the capture ``deterministic=False``.
6. Per frame ``i``: advance the fake clock to ``round(t_i)`` (one ``run_for`` call, so timers and rAF
   callbacks fire), wait for the network, seek every animation, SVG timeline and video (one
   ``evaluate`` call), force a style flush, wait for the network again, screenshot. SPEC 6.2 lists the
   steps as "seek, then ``run_for``, then screenshot"; both orders show every clock at ``t_i`` in the
   picture. The clock is advanced first so that an animation created by a timer or rAF callback during
   that step is found and seeked before the picture instead of one frame later.
7. Double-shot check at frame 0 and the last frame: a second screenshot 100 ms of real time later, at
   the same virtual time, must be identical, otherwise something animates on a clock we do not control
   and the capture is marked ``deterministic=False``. A timed-out ``<video>`` seek or animation-ready
   wait also marks it ``deterministic=False`` (SPEC 6.2).
8. ``Math.random`` is replaced by a fixed-seed generator before any page script runs (ADR-022).
9. Ablation (ADR-013): animations on elements matching ``settings.suppress_selectors`` are held at
   their frame-0 state; ones created on them after frame 0 are cancelled; media and elements that only
   script can move are hidden (see ``capture/page_scripts.py``).

Time base. ``t`` above is virtual time since the page loaded. Without an interaction script frame ``i``
sits at ``t_i = i * 1000 / fps`` ms. With one, the script's virtual time (the pre-roll ``p``) comes first,
frame ``i`` sits at ``p + t_i``, and animations started during the script keep running into the frames,
exactly as the page's JavaScript clock does. The seek pass also runs at every pump of the interaction
script, so an animation the script waits for finishes at a virtual time, not a real one.

Two facts about Playwright 1.63's fake clock shaped the implementation:

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
from dataclasses import dataclass, field
from typing import Any, ClassVar

from playwright.sync_api import BrowserContext, Page

from capture.page_scripts import (
    ANIMATION_RECORDER_JS,
    PAGE_HELPERS_JS,
    SEEDED_RANDOM_JS,
    SEEDED_RANDOM_NOTE,
    TIMELINE_CLOCK_JS,
    TIMELINE_CLOCK_NOTE,
)
from capture.strategies import network_wait
from capture.strategies.base import (
    DOUBLE_SHOT_REAL_GAP_MS,
    CaptureSettings,
    CaptureStrategy,
    FrameSink,
    ProgressCallback,
    StrategyOutcome,
    double_shot_matches,
    take_screenshot,
)
from capture.strategies.network_wait import NetworkTracker

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

#: Real milliseconds strategy A waits for ``animation.ready`` and for a ``<video>``'s ``seeked`` event.
#: When either wait runs out the frame may show a state that depends on real time, so the capture is
#: marked ``deterministic=False`` (SPEC 6.2).
READY_TIMEOUT_MS = 500
VIDEO_SEEK_TIMEOUT_MS = 2000

#: Real milliseconds a ``<video>`` or ``<img>`` in the viewport that is still loading may take to arrive.
#: One that takes longer would appear at a frame set by network speed: deterministic=False.
MEDIA_READY_TIMEOUT_MS = 5000

#: Real milliseconds to wait for one browser rendering frame after finishing an animation, so the page's
#: ``finish`` handlers have run (see ``letFinishHandlersRun`` below).
FINISH_WAIT_MS = 1000

#: Most links of a chain of animations (each started by the previous one's finish handler) followed
#: within one pass. A chain still going after that is reported (deterministic=False).
MAX_FINISH_ROUNDS = 8

# Per-frame page-side function, also run at every pump of the interaction script. ``PAGE_HELPERS_JS``
# (capture/page_scripts.py) supplies ``ftScopes`` (the document, every shadow root and every
# same-origin frame), ``ftOwnCall`` and the ablation helpers. Arguments:
#   tMs     virtual milliseconds since the page loaded at which this pass runs (see "Time base" above);
#   stage   'pre-roll' (interaction pump), 'frame0' or 'later' (any frame after frame 0);
#   walk    true to also walk every element for undeclared open shadow roots (costly: first pass only);
#   suppress, limits  the ablation selectors and the real-time limits (ms) of the waits.
# The body waits for loading media, registers animations it has not seen before (pausing them and
# remembering when they first appeared), applies what the page asked for (pause, resume, a new current
# time), seeks every animation, SVG timeline and <video>, then forces a style/layout flush so the
# screenshot shows the seeked state. Returns counters for the capture notes.
_SEEK_FRAME_BODY = r"""
  const W = window;
  const registry = W.__ftRegistry || (W.__ftRegistry = { nextId: 1, videos: new WeakSet() });
  const stats = { animations: 0, newAnimations: 0, pagePaused: 0, pageHeld: 0, pageSeeks: 0, readopted: 0,
                  unseekable: 0, finished: 0, finishTimeouts: 0, readyTimeouts: 0, suppressedHeld: 0,
                  suppressedCancelled: 0, shadowRoots: 0, svgTimelines: 0, svgPagePaused: 0, svgHeld: 0,
                  svgResumed: 0, videos: 0, videosNotReady: 0, videosResumed: 0, videoSeeks: 0,
                  videoSeekTimeouts: 0, videoWaitSkipped: 0, mediaWaits: 0, mediaTimeouts: 0, perfNow: 0,
                  rebase: W.__ftClockRebase || 'missing' };
  const late = stage === 'later';  // after frame 0

  // Where to look (ADR-022).
  const scopes = ftScopes(W, walk);
  stats.shadowRoots = scopes.filter((scope) => scope.root.nodeType === 11).length;  // 11: a shadow root
  // Ablation (ADR-013): mark the elements matching a suppressed selector and hide media and
  // script-driven elements on them. Their animations are held (or cancelled) further down.
  if (suppress.length) ftApplySuppression(suppress, scopes, false);

  // Real-time waits. The page's setTimeout and requestAnimationFrame are fake and frozen, so waits use
  // the browser's originals, which Playwright keeps at __pwClock.builtins.
  const builtins = W.__pwClock && W.__pwClock.builtins;
  const realSetTimeout = builtins && typeof builtins.setTimeout === 'function' ? builtins.setTimeout : null;
  const realFrame = builtins && typeof builtins.requestAnimationFrame === 'function' ? builtins.requestAnimationFrame : null;
  // Settles like promise, or with 'timeout' after ms of real time (at once when there is no real timer,
  // because then a wait could never be bounded).
  const waitAtMost = (promise, ms) => realSetTimeout
    ? Promise.race([promise, new Promise((resolve) => realSetTimeout(() => resolve('timeout'), ms))])
    : Promise.resolve('timeout');
  const allReady = (list) => waitAtMost(Promise.all(list.map((a) => a.ready.catch(() => undefined))), limits.readyMs);

  // ---- Media still loading (SPEC 6.2) ----
  // An <img> or <video> that is still loading and overlaps the viewport is waited for, so it appears at
  // the pass in which the page asked for it, however slow the network. One that never arrives is
  // counted (deterministic=false) and not waited for again. Off-screen media cannot reach the picture.
  const inViewport = (el) => {
    const box = el.getBoundingClientRect();
    const view = el.ownerDocument.defaultView;
    return box.width > 0 && box.height > 0 && box.right > 0 && box.bottom > 0 && box.left < view.innerWidth && box.top < view.innerHeight;
  };
  const stillLoading = (el) => {
    if (el.__ftGaveUp) return false;
    // A video loads while networkState is 2 (NETWORK_LOADING); readyState 2 (HAVE_CURRENT_DATA) means a
    // picture is available. A video with preload="none" never loads and simply shows its poster.
    const loading = el.localName === 'img' ? !el.complete : el.networkState === 2 && el.readyState < 2 && !el.error;
    return loading && !ftIsHidden(el) && inViewport(el);
  };
  const arrival = (el) => el.localName === 'img'
    ? el.decode().then(() => 'ok', () => 'ok')  // decode() settles once the image is loaded and decoded, or failed
    : new Promise((resolve) => {
      el.addEventListener('loadeddata', () => resolve('ok'), { once: true });
      el.addEventListener('error', () => resolve('ok'), { once: true });
    });
  const waitForMedia = async () => {
    const pending = [];
    for (const { root } of scopes) {
      for (const el of root.querySelectorAll('img, video')) { if (stillLoading(el)) pending.push(el); }
    }
    if (!pending.length) return;
    stats.mediaWaits += pending.length;
    if (await waitAtMost(Promise.all(pending.map(arrival)), limits.mediaMs) !== 'timeout') return;
    for (const el of pending) {
      if (stillLoading(el)) { el.__ftGaveUp = true; stats.mediaTimeouts++; }
    }
  };

  // ---- Animations ----
  // The current time the page assigned (through currentTime or startTime) since we last looked, or
  // null. ANIMATION_RECORDER_JS writes it; reading it clears it.
  const takePageSeek = (animation) => {
    const value = animation.__ftPageSeek;
    animation.__ftPageSeek = undefined;
    return typeof value === 'number' && Number.isFinite(value) ? value : null;
  };
  const isSuppressed = (animation) => {
    const target = animation.effect && animation.effect.target;
    return suppress.length > 0 && Boolean(target) && ftIsSuppressed(target);
  };

  // An animation the page holds paused stays where the page put it; its time is remembered so that it
  // continues from there if the page resumes it. Returns false: it is not ours to seek.
  const leaveAlone = (animation) => {
    animation.__ft.pausedAt = animation.currentTime;
    stats.pagePaused++;
    return false;
  };

  // First sighting (SPEC 6.2). An animation starts at the pass in which it first appears, from the
  // current time the page gave it (its phase), if any. One the page holds paused is left alone
  // (ADR-013). One on a suppressed element is held still: if it exists by frame 0 it keeps its frame-0
  // state; if it appears later it is cancelled, because its frame-0 state is the element's own style.
  // Returns true when the animation is to be seeked.
  const register = (animation, fresh) => {
    const suppressed = isSuppressed(animation);
    if (late && suppressed) {
      animation.cancel();
      stats.suppressedCancelled++;
      return false;
    }
    if (late) stats.newAnimations++;
    const pagePaused = animation.playState === 'paused' && !suppressed;
    animation.__ftId = registry.nextId++;
    animation.__ft = { offset: tMs, phase: takePageSeek(animation), held: null, last: null, pausedAt: null, pagePaused };
    if (pagePaused) return leaveAlone(animation);
    fresh.push(animation);
    return true;
  };

  // ADR-022: we paused this animation and the page set it running again (play(), a replay from a
  // timer). Left alone it would run on the real clock, so it is paused and seeked again. If the page
  // rewound it (its time is now before where we last put it) it starts again at this pass, from its
  // beginning; otherwise it keeps its start.
  const readopt = (animation, fresh) => {
    const ft = animation.__ft;
    const now = animation.currentTime;
    if (animation.playbackRate > 0 && typeof ft.last === 'number' && typeof now === 'number' && now < ft.last) {
      Object.assign(ft, { offset: tMs, phase: null });
    }
    stats.readopted++;
    fresh.push(animation);
  };

  // An animation seen at an earlier pass. Returns true when it is to be seeked.
  const recheck = (animation, fresh) => {
    const ft = animation.__ft;
    if (ft.pagePaused) {
      if (animation.playState === 'paused') return leaveAlone(animation);
      // The page resumed it: from now on we drive it, continuing from where it stood.
      Object.assign(ft, { pagePaused: false, offset: tMs, phase: ft.pausedAt, held: null });
      fresh.push(animation);
      return true;
    }
    const seek = takePageSeek(animation);
    if (seek !== null) {
      // The page moved it to a new time: its clock restarts here, from that time (a held one stays held).
      Object.assign(ft, { offset: tMs, phase: seek, held: ft.held === null ? null : 0 });
      stats.pageSeeks++;
      fresh.push(animation);
    } else if (animation.playState === 'running') readopt(animation, fresh);
    return true;
  };

  // Pass 1: find the animations of every scope that this pass has not handled yet (seen carries over
  // between the rounds of driveAnimations); sort out which to seek and which to pause first.
  const collect = (seen) => {
    const found = [];  // animations to seek
    const fresh = [];  // animations that must be paused first (new, or set running or moved by the page)
    for (const { root } of scopes) {
      let animations = [];
      try { animations = root.getAnimations(); } catch (e) { continue; }
      for (const animation of animations) {
        if (seen.has(animation)) continue;
        seen.add(animation);
        stats.animations++;
        const ours = animation.__ftId === undefined ? register(animation, fresh) : recheck(animation, fresh);
        if (ours) found.push(animation);
      }
    }
    return { found, fresh };
  };

  // Pass 2: pause with a handshake. Measured on Chromium 1243: pausing an animation that is still
  // starting ("play-pending") and seeking it in the same task leaves the compositor one frame ahead of
  // the seeked time (an opacity fader showed 1/60 s of progress at t=0, and the amount varied between
  // runs). Letting the animation start (await ready), pausing, waiting for the pause to be applied
  // (await ready again) and only then seeking gives the exact seeked state every time. A wait that
  // runs out is counted: the frame may then be one compositor frame off (SPEC 6.2).
  const pauseFresh = async (fresh) => {
    if (!fresh.length) return;
    if (await allReady(fresh) === 'timeout') stats.readyTimeouts++;
    for (const animation of fresh) { try { ftOwnCall(animation, () => animation.pause()); } catch (e) {} }
    if (await allReady(fresh) === 'timeout') stats.readyTimeouts++;
  };

  // CSS animation-play-state: paused on a CSS animation's element. Once we have paused a CSS animation
  // through the Web Animations API the browser no longer applies this property to it (ADR-013), so we
  // read it from the computed style and apply it ourselves.
  const cssPaused = (animation) => {
    const target = animation.effect && animation.effect.target;
    if (typeof animation.animationName !== 'string' || !target) return false;  // not a CSS animation
    let style = null;
    try { style = target.ownerDocument.defaultView.getComputedStyle(target, animation.effect.pseudoElement); } catch (e) { return false; }
    const names = style.animationName.split(',').map((name) => name.trim());
    const states = style.animationPlayState.split(',').map((state) => state.trim());
    const index = names.indexOf(animation.animationName);
    return index >= 0 && states[index % states.length] === 'paused';  // the CSS list repeats when shorter
  };

  // The animation must stand still at this pass: the page paused it (pause(), or CSS
  // animation-play-state), or its element is suppressed for an ablation (ADR-013 (a)).
  const mustHold = (animation) => animation.__ftPageWantsPaused === true || isSuppressed(animation) || cssPaused(animation);

  // Local time of a driven item (an animation or an SVG timeline): virtual time since it started,
  // frozen while hold is true. Freezing slides the start forward, so once the hold ends the item
  // continues from where it stood.
  const localTime = (ft, hold) => {
    if (hold && ft.held === null) ft.held = Math.max(0, tMs - ft.offset);  // the hold begins at this pass
    if (ft.held !== null) ft.offset = tMs - ft.held;                        // held, or released at this pass
    if (!hold) ft.held = null;
    return Math.max(0, tMs - ft.offset);
  };

  const effectEnd = (animation) => {
    try { return animation.effect.getComputedTiming().endTime; } catch (e) { return Infinity; }
  };

  // Where the animation should be: phase + rate x local time. The phase is the current time the page
  // gave it; without one it is 0, or the effect's end for a reversed (negative-rate) animation, which
  // plays from its end towards 0.
  const seekTarget = (animation, hold) => {
    const rate = typeof animation.playbackRate === 'number' ? animation.playbackRate : 1;
    const end = effectEnd(animation);
    const phase = animation.__ft.phase !== null ? animation.__ft.phase : (rate < 0 && Number.isFinite(end) ? end : 0);
    const target = phase + rate * localTime(animation.__ft, hold);
    return rate < 0 ? Math.max(0, target) : target;
  };

  // True when target lies at or past the end of the effect, in the direction the animation plays.
  const reachedEnd = (animation, target) => {
    if (animation.playbackRate > 0) return target >= effectEnd(animation);
    return animation.playbackRate < 0 && target <= 0;
  };

  // Pass 3: seek one animation. A paused animation never finishes by itself, so its 'finished' promise
  // and 'finish' event, which pages use to start their next animation, would never come. So when an
  // animation we drive reaches its end, it is finished through the API, once. A held animation is not
  // finished: a paused animation stays paused at its end. Returns true when it finished at this pass.
  const seekOne = (animation, hold) => {
    const target = seekTarget(animation, hold);
    if (hold) stats[isSuppressed(animation) ? 'suppressedHeld' : 'pageHeld']++;
    try {
      ftOwnCall(animation, () => { animation.currentTime = target; });
      animation.__ft.last = target;
    } catch (e) { stats.unseekable++; return false; }
    if (hold || animation.playState === 'finished' || !reachedEnd(animation, target)) return false;
    try { animation.finish(); } catch (e) { return false; }
    stats.finished++;
    return true;
  };

  // After finish(), the page's 'finished' promise callbacks run at once and its 'finish' event handlers
  // at the browser's next rendering update, before that update's requestAnimationFrame callbacks.
  // Waiting for one real rendering frame therefore lets both run now, so an animation they start is
  // registered at this pass instead of at a later, real-time-dependent one. False if no frame came.
  const letFinishHandlersRun = async () => {
    const frame = realFrame ? new Promise((resolve) => realFrame(() => resolve('ok'))) : Promise.resolve('timeout');
    return (await waitAtMost(frame, limits.finishMs)) !== 'timeout';
  };

  // Passes 1-3, repeated while finishing an animation makes the page start another (each round handles
  // only animations the earlier rounds did not see). Styles are read for every animation before any is
  // seeked, so the browser recalculates style once, not once per animation. A chain still going after
  // the last round, or a rendering frame that never came, is counted (deterministic=false).
  const driveAnimations = async () => {
    const seen = new Set();
    for (let round = 1; ; round++) {
      const { found, fresh } = collect(seen);
      await pauseFresh(fresh);
      const holds = found.map((animation) => mustHold(animation));
      const finishedNow = found.filter((animation, i) => seekOne(animation, holds[i])).length;
      if (finishedNow === 0) return;
      if (round >= limits.finishRounds || !(await letFinishHandlersRun())) {
        stats.finishTimeouts++;
        return;
      }
    }
  };

  // ---- SVG SMIL (ADR-022) ----
  // One SVG timeline. A timeline the page paused itself stands still: one already paused when we
  // first see it is left alone until the page un-pauses it (it then continues from where it stood);
  // one the page pauses later (recorded by ANIMATION_RECORDER_JS) is held. A timeline the page
  // un-pauses after we paused it is paused again. Returns false when the page holds it paused.
  const driveSvg = (svg) => {
    const ft = svg.__ft;
    if (ft.pagePaused) {
      if (svg.animationsPaused()) { ft.phase = svg.getCurrentTime() * 1000; stats.svgPagePaused++; return false; }
      Object.assign(ft, { pagePaused: false, offset: tMs, held: null });
    } else if (!svg.animationsPaused()) stats.svgResumed++;
    const hold = svg.__ftPageWantsPaused === true;
    if (hold) stats.svgHeld++;
    const seconds = (ft.phase + localTime(ft, hold)) / 1000;
    ftOwnCall(svg, () => svg.pauseAnimations());
    svg.setCurrentTime(seconds);
    return true;
  };

  // Pass 4: each outermost <svg> owns one animation timeline, which neither the fake clock nor
  // getAnimations() reaches. It is paused and set to the pass time, in seconds. A timeline held for an
  // ablation stays at 0 (page_scripts.py).
  const seekSvgTimelines = () => {
    for (const { root } of scopes) {
      for (const svg of root.querySelectorAll('svg')) {
        if (svg.ownerSVGElement !== null || typeof svg.setCurrentTime !== 'function' || svg.__ftSmilHeld) continue;
        if (!svg.__ft) svg.__ft = { offset: tMs, phase: 0, held: null, pagePaused: svg.animationsPaused() };
        if (driveSvg(svg)) stats.svgTimelines++;
      }
    }
  };

  // Pass 5: videos are paused (again, if the page started them), muted and seeked to the pass time.
  const seekVideos = () => {
    const pending = [];
    for (const { root } of scopes) {
      for (const video of root.querySelectorAll('video')) {
        stats.videos++;
        if (video.hasAttribute('data-ft-suppressed-media')) continue;  // hidden for an ablation
        if (!registry.videos.has(video)) {
          registry.videos.add(video);
          try { video.pause(); video.muted = true; video.autoplay = false; } catch (e) {}
        } else if (!video.paused) {
          try { video.pause(); } catch (e) {}
          stats.videosResumed++;
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
        pending.push(new Promise((resolve) => {
          const done = () => { video.removeEventListener('seeked', done); resolve(true); };
          video.addEventListener('seeked', done);
          video.currentTime = target;
        }));
      }
    }
    return pending;
  };

  await waitForMedia();
  await driveAnimations();
  seekSvgTimelines();
  const pendingSeeks = seekVideos();
  // Reading offsetHeight forces style recalculation and layout, so the seeked values are applied
  // before the screenshot rather than at some later browser-chosen moment.
  for (const { root } of scopes) {
    if (root.nodeType === 9) { try { void root.documentElement.offsetHeight; } catch (e) {} }  // 9: a document
  }
  stats.perfNow = W.performance.now();

  if (pendingSeeks.length === 0) return stats;
  if (!realSetTimeout) {
    // Without a real timer we cannot bound the wait for 'seeked', so do not wait at all.
    stats.videoWaitSkipped = pendingSeeks.length;
    return stats;
  }
  const outcome = await waitAtMost(Promise.all(pendingSeeks).then(() => 'ok'), limits.videoSeekMs);
  if (outcome === 'timeout') stats.videoSeekTimeouts = pendingSeeks.length;
  return stats;
"""

SEEK_FRAME_JS = (
    "async ({ tMs, stage, walk, suppress, limits }) => {\n"
    + PAGE_HELPERS_JS
    + _SEEK_FRAME_BODY
    + "}\n"
)

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


def _frames_text(frames: list[int]) -> str:
    """'3 frame(s), first at frame 12' for a list of frame indices."""
    return f"{len(frames)} frame(s), first at frame {frames[0]}"


def _uncertainty_texts() -> dict[str, str]:
    """Per-pass counters that each make the capture non-deterministic when non-zero (SPEC 6.2: a wait
    that runs out means the picture may show a state that depends on real time), with what they mean.

    A function, not a constant, so the texts always show the limits in force.
    """
    return {
        "readyTimeouts": f"animation-ready wait timed out after {READY_TIMEOUT_MS} ms of real time",
        "videoSeekTimeouts": f"<video> seek did not report 'seeked' within {VIDEO_SEEK_TIMEOUT_MS} ms of real time",
        "videoWaitSkipped": "<video> seek could not be awaited (no original timer available)",
        "mediaTimeouts": f"a <video> or <img> in the viewport was still loading after {MEDIA_READY_TIMEOUT_MS} ms of real time",
        "finishTimeouts": (
            f"the page's animation 'finish' handlers did not settle (no rendering frame within {FINISH_WAIT_MS} ms "
            f"of real time, or a chain of more than {MAX_FINISH_ROUNDS} animations)"
        ),
    }


# Counters summed over every frame, and counters for which the frames where they were non-zero are kept.
_SUMMED = ("newAnimations", "unseekable", "suppressedCancelled", "svgResumed", "videosResumed", "finished", "pageSeeks")
_PER_FRAME = ("readopted", "pageHeld", "svgHeld")


@dataclass
class _Tally:
    """Counters from ``SEEK_FRAME_JS``, summed over one capture (frames and pre-roll) and turned into notes."""

    first: dict[str, Any] | None = None  # the frame-0 counters
    sums: dict[str, int] = field(default_factory=dict)
    frames: dict[str, list[int]] = field(default_factory=dict)  # counter -> frames where it was non-zero
    preroll: list[str] = field(default_factory=list)  # uncertainty counters non-zero during the pre-roll
    abandoned: list[tuple[str, str]] = field(default_factory=list)  # (where, URL) of requests given up on

    def add(self, index: int, stats: dict[str, Any], abandoned: list[str]) -> None:
        """Fold in the counters of frame ``index`` and the requests given up on before its picture."""
        if self.first is None:
            self.first = stats
        for key in _SUMMED:
            self.sums[key] = self.sums.get(key, 0) + int(stats[key])
        for key in _PER_FRAME + tuple(_uncertainty_texts()):
            if stats[key]:
                self.frames.setdefault(key, []).append(index)
        self.abandoned.extend((f"frame {index}", url) for url in abandoned)

    def add_preroll(self, stats: dict[str, Any], abandoned: list[str]) -> None:
        """Fold in one interaction-script pump: only the waits that ran out matter there."""
        self.preroll.extend(key for key in _uncertainty_texts() if stats[key] and key not in self.preroll)
        self.abandoned.extend(("the interaction pre-roll", url) for url in abandoned)

    def uncertainties(self) -> list[str]:
        """Waits that ran out (SPEC 6.2): each one makes the capture non-deterministic."""
        texts = _uncertainty_texts()
        found = [f"{text} at {_frames_text(self.frames[key])}; deterministic=false" for key, text in texts.items() if key in self.frames]
        found += [f"during the interaction pre-roll: {texts[key]}; deterministic=false" for key in self.preroll]
        if self.abandoned:
            where = ", ".join(f"{url} ({place})" for place, url in self.abandoned)
            found.append(
                f"network request(s) still unfinished after {network_wait.NETWORK_WAIT_MS} ms of real time were "
                f"no longer waited for: {where}; deterministic=false"
            )
        return found

    def _frame_notes(self) -> list[tuple[Any, str]]:
        """(present, text) for the notes that describe frame 0."""
        first = self.first or {}
        return [
            (first.get("shadowRoots"), f"{first.get('shadowRoots')} shadow root(s) (open or closed) searched for animations and videos"),
            (first.get("svgTimelines"), f"{first.get('svgTimelines')} SVG SMIL timeline(s) paused and set to the frame time"),
            (first.get("pagePaused"), f"{first.get('pagePaused')} animation(s) were already paused by the page and were left paused"),
            (first.get("svgPagePaused"), f"{first.get('svgPagePaused')} SVG timeline(s) were already paused by the page and were left paused"),
            (first.get("suppressedHeld"), f"{first.get('suppressedHeld')} animation(s) on suppressed elements held at their frame-0 state"),
            (first.get("videos"), f"{first.get('videos')} video element(s) paused, muted and seeked per frame"),
            (first.get("videosNotReady"), f"{first.get('videosNotReady')} video element(s) had no metadata and could not be seeked"),
        ]

    def _capture_notes(self) -> list[tuple[Any, str]]:
        """(present, text) for the notes that sum up the whole frame loop."""
        sums, frames = self.sums, self.frames
        at = {key: _frames_text(frames[key]) for key in frames}
        return [
            ("readopted" in at, f"animation(s) set running again by the page were re-adopted (paused and seeked) at {at.get('readopted')}"),
            ("pageHeld" in at, f"animation(s) paused by the page (pause() or CSS animation-play-state) were held still at {at.get('pageHeld')}"),
            ("svgHeld" in at, f"SVG timeline(s) paused by the page were held still at {at.get('svgHeld')}"),
            (sums.get("pageSeeks"), f"{sums.get('pageSeeks')} current time(s) assigned by the page were applied"),
            (sums.get("finished"), f"{sums.get('finished')} animation(s) reached their end and were finished, so the page's finish handlers ran"),
            (sums.get("svgResumed"), f"{sums.get('svgResumed')} SVG timeline un-pause(s) by the page were undone"),
            (sums.get("videosResumed"), f"{sums.get('videosResumed')} video play() call(s) by the page were undone (paused again before seeking)"),
            (sums.get("suppressedCancelled"), f"{sums.get('suppressedCancelled')} animation(s) created after frame 0 on suppressed elements were cancelled"),
            (sums.get("unseekable"), f"{sums.get('unseekable')} animation seek(s) were rejected by the browser (for example scroll-driven timelines)"),
        ]

    def notes(self) -> list[str]:
        """Plain facts about what was driven, for ``meta.notes``."""
        first = self.first or {}
        notes = [f"{first.get('animations', 0)} animation(s) found at frame 0, {self.sums.get('newAnimations', 0)} discovered later"]
        return notes + [text for present, text in self._frame_notes() + self._capture_notes() if present]

    def failures(self) -> list[str]:
        """Set-up problems that make the frames unreliable and should send "auto" to the next strategy."""
        rebase = (self.first or {}).get("rebase", "missing")
        return [] if rebase == "ok" else [f"fake clock re-base unavailable ({rebase}); page time origin may vary"]


class WaapiStepStrategy(CaptureStrategy):
    """Strategy A. See the module docstring for the algorithm."""

    name: ClassVar[str] = "waapi_step"
    deterministic_by_design: ClassVar[bool] = True
    requires_controllable_content: ClassVar[bool] = True
    reapplies_suppression: ClassVar[bool] = True

    def __init__(self) -> None:
        # Virtual milliseconds already consumed before frame 0 (by the interaction script).
        self._preroll_ms = 0
        self._context: BrowserContext | None = None
        self._settings: CaptureSettings | None = None
        self._network = NetworkTracker()
        self._tally = _Tally()

    # ------------------------------------------------------------------ setup
    def prepare_context(self, context: BrowserContext, settings: CaptureSettings) -> None:
        """Install and pause the fake clock at virtual 0 before any page exists; add the init scripts.

        Doing this on the context (no pages yet) means every document, including the first one,
        starts with the clock already paused; no real time can leak into page-visible time. The init
        scripts re-base the clock, seed ``Math.random``, put ``document.timeline`` on the fake clock and
        record what the page asks of its animations (``capture/page_scripts.py``).
        """
        self._context = context
        self._settings = settings
        context.clock.install(time=0)
        context.clock.pause_at(0)
        for script in (CLOCK_REBASE_JS, SEEDED_RANDOM_JS, TIMELINE_CLOCK_JS, ANIMATION_RECORDER_JS):
            context.add_init_script(script)

    def prepare_page(self, page: Page, settings: CaptureSettings) -> None:
        """Start counting the page's network requests before it loads (``network_wait.py``)."""
        self._network.attach(page)

    def _advance_clock(self, milliseconds: int) -> None:
        """Move virtual time forward, firing every timer and rAF callback that falls due."""
        if self._context is None:
            raise RuntimeError("prepare_context() must run before the clock is advanced")
        self._context.clock.run_for(int(milliseconds))

    def _seek_pass(self, page: Page, t_ms: float, stage: str, walk: bool) -> tuple[dict[str, Any], list[str]]:
        """Let the network settle, run ``SEEK_FRAME_JS`` at virtual time ``t_ms``, let the network settle again.

        The second wait covers requests the pass itself started (a style flush can start an image
        load). Returns the page's counters and the URLs of requests given up on (``network_wait.py``).
        Suppression starts at frame 0: the ablation set-up runs after the interaction script.
        """
        if self._settings is None:
            raise RuntimeError("prepare_context() must run before the first pass")
        self._network.wait_for_idle(page)
        stats: dict[str, Any] = page.evaluate(
            SEEK_FRAME_JS,
            {
                "tMs": t_ms,
                "stage": stage,
                "walk": walk,
                "suppress": [] if stage == "pre-roll" else list(self._settings.suppress_selectors),
                "limits": {
                    "readyMs": READY_TIMEOUT_MS,
                    "videoSeekMs": VIDEO_SEEK_TIMEOUT_MS,
                    "mediaMs": MEDIA_READY_TIMEOUT_MS,
                    "finishMs": FINISH_WAIT_MS,
                    "finishRounds": MAX_FINISH_ROUNDS,
                },
            },
        )
        self._network.wait_for_idle(page)
        return stats, self._network.take_abandoned()

    # ------------------------------------------------------------------ interaction script
    def run_interaction(self, page: Page, script: str) -> list[str]:
        """Run the interaction script, pumping virtual time until its promise settles.

        Page timers are frozen, so ``await new Promise(r => setTimeout(r, 500))`` inside the script
        would hang forever if we simply awaited it. Instead the script is started, and while it is
        still pending the clock is advanced in 16 ms steps. Before each look at the script the seek
        pass runs at the virtual time reached so far: the network settles, and animations are paused,
        seeked and finished on virtual time, so a script that awaits a request or an animation's
        ``finished`` promise ends after the same number of pumps in every run. The virtual time
        consumed is remembered as the pre-roll: frame 0 then sits at ``preroll_ms`` (module docstring).
        """
        notes: list[str] = []
        page.evaluate(START_INTERACTION_JS, script)
        deadline = time.monotonic() + INTERACTION_REAL_CAP_S
        # Virtual time already walked before the script: 0 for a capture; capture/inspect.py walks
        # strategy B's pre-roll first when it inspects a page that B captured.
        start = self._preroll_ms
        pumped = 0
        while True:
            now = start + pumped
            self._tally.add_preroll(*self._seek_pass(page, now, "pre-roll", walk=now == 0))
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
        self._preroll_ms = start + pumped
        if pumped:
            notes.append(f"interaction script consumed {pumped} ms of virtual time before frame 0")
        return notes

    # ------------------------------------------------------------------ frames
    def _clock_failure(self, stats: dict[str, Any], index: int, target_ms: int) -> list[str]:
        """The page's JavaScript clock must read exactly the virtual time we set.

        Anything else means real time leaked in, and rAF-driven pages would not reproduce.
        """
        expected_now = self._preroll_ms + target_ms
        if stats["perfNow"] == expected_now:
            return []
        return [f"page clock read {stats['perfNow']} ms at frame {index}, expected {expected_now} ms"]

    def run(
        self,
        page: Page,
        settings: CaptureSettings,
        sink: FrameSink,
        progress: ProgressCallback | None,
    ) -> StrategyOutcome:
        total = settings.frame_count
        failures: list[str] = []
        clock_ms = 0  # virtual ms advanced since frame-loop start (excludes the pre-roll)

        for index in range(total):
            target_ms = int(round(settings.frame_time_ms(index)))
            # Frame 0 still issues run_for(0): it fires timers already due at virtual 0 so the page
            # is in the same state whether or not a due timer happened to fire during setup.
            self._advance_clock(target_ms - clock_ms)
            clock_ms = target_ms
            pass_ms = self._preroll_ms + settings.frame_time_ms(index)  # virtual time since load
            stats, abandoned = self._seek_pass(page, pass_ms, "frame0" if index == 0 else "later", walk=index == 0)
            self._tally.add(index, stats, abandoned)
            checked = index in (0, total - 1)  # SPEC 6.2: the checks run at frame 0 and the last frame
            if checked:
                failures.extend(self._clock_failure(stats, index, target_ms))
            digest = sink.add(take_screenshot(page))
            if checked and not double_shot_matches(page, digest):
                failures.append(
                    f"double-shot check failed at frame {index}: two screenshots at the same virtual time, "
                    f"{DOUBLE_SHOT_REAL_GAP_MS} ms of real time apart, differ"
                )
            if progress is not None:
                progress(index + 1, total)

        return self._outcome(failures, settings.fps)

    def _outcome(self, failures: list[str], fps: int) -> StrategyOutcome:
        """Turn the per-frame counters and check failures into the strategy's verdict and notes.

        A failure (clock, double-shot) sends "auto" on to the next strategy; a wait that ran out only
        makes this capture non-deterministic (SPEC 6.2).
        """
        tally = self._tally
        failures = failures + tally.failures()
        uncertain = tally.uncertainties()
        notes = tally.notes() + [
            SEEDED_RANDOM_NOTE,
            TIMELINE_CLOCK_NOTE,
            f"fake clock stepped in whole ms: frame i sits at round(i*1000/{fps}) ms, animations seeked to the exact time",
        ]
        return StrategyOutcome(
            deterministic=not failures and not uncertain,
            notes=notes + uncertain + failures,
            failure_reason="; ".join(failures) if failures else None,
        )
