"""Candidate collection for attribution (SPEC.md section 7, ADR-013).

Attribution has to answer "which element on the page made this region flash?". The first step is a list
of *candidates*: elements that could be responsible. This module loads the page again, fresh, and puts it
in exactly the state it was in at the captured frame of interest, then asks the page three questions:

1. Which animations and transitions exist? (``getAnimations()`` on the document, on every recorded
   shadow root and on every same-origin frame.) Each one's target element is a candidate, together with
   the animation's name, duration and keyframes.
2. Which elements sit under the failing window? (``elementsFromPoint`` on a 5 x 5 grid of points inside
   the violation's window; every element of every stack counts.)
3. Which ``<video>``, ``<canvas>``, animated ``<img>`` and ``<iframe>`` elements are there? (Animated
   images are recognised from their bytes by ``capture/detect.py``; a cross-origin iframe becomes an
   "unattributable region" candidate, because what happens inside it cannot be seen.)

``<html>`` and ``<body>`` are never candidates: they cover everything, so they would explain nothing.

Getting "exactly the same state" is the reason this module does not write its own page set-up. It runs
the capturer's own per-attempt steps (``capturer._run_attempt``: fresh context, init scripts, fake clock,
page load, fonts, interaction script, detection) with ``InspectStrategy``, a copy of strategy A
(``waapi_step``) that walks the frames with the same clock steps and animation seeks as a capture, but
stops at the requested frames to collect candidates instead of taking screenshots.

The time base follows the strategy that made the audited frames. Strategy A's frame ``i`` sits at
``t_i = i * 1000 / fps`` ms after load (after the interaction script, if any). Strategy B pre-rolls the
page to a fixed time before frame 0 (SPEC 6.2, ADR-023), so the caller passes that pre-roll as
``lead_in_ms`` and inspection walks it first. Strategy C's frame 0 is its first screencast picture, at a
real time nobody controls; no lead-in can match it, and ``capture/attribute.py`` records that as a
limitation.

Each candidate is tagged ``data-ft-id="ft-<n>"`` (n counts up in document order), gets a CSS selector
that matches it alone (``#id`` when that id is unique, otherwise a short ``tag:nth-of-type`` path from
the nearest ancestor with a unique id), its bounding rectangle at that moment, and its tag name. An
element inside a shadow root or a same-origin frame gets its host's (or frame element's) selector in
front, joined by `` >>> `` (``live-banner >>> div``): the capturer's ablation reads that form
(``ftQueryDeep`` in ``capture/page_scripts.py``), and a stylesheet of the top document, which cannot
reach such an element, ignores it instead of restyling some other element.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from playwright.sync_api import Browser, Page

from capture.capturer import _Attempt, _run_attempt, resolve_url
from capture.detect import _classify_image, local_reads_allowed
from capture.page_scripts import PAGE_HELPERS_JS
from capture.strategies.base import CaptureSettings, FrameSink, ProgressCallback, StrategyOutcome
from capture.strategies.waapi_step import INTERACTION_PUMP_MS, WaapiStepStrategy

#: SPEC 7: "elementsFromPoint on a 5x5 grid inside the window".
POINT_GRID = 5

#: What a cross-origin iframe is reported as: its pixels can be seen, its contents cannot (SPEC 10).
CROSS_ORIGIN_NODE_TYPE = "iframe (cross-origin, unattributable)"

#: Elements whose pixels can change without any CSS or Web Animation: video frames, canvas drawing and
#: frame documents.
SELF_PAINTING_TAGS = ("video", "canvas", "iframe", CROSS_ORIGIN_NODE_TYPE)

# The page-side half of the collection. PAGE_HELPERS_JS (capture/page_scripts.py) supplies ftScopes, the
# same walk over the document, shadow roots and same-origin frames that capture and detection use.
_COLLECT_BODY = r"""
  const scopes = ftScopes(window, true);

  // Position of every element in document order (document first, then frames, then shadow roots).
  const order = new Map();
  for (const { root } of scopes) {
    for (const el of root.querySelectorAll('*')) if (!order.has(el)) order.set(el, order.size);
  }

  // One entry per element, remembering why it became a candidate.
  const found = new Map();
  const add = (el, source) => {
    if (!el || el.nodeType !== 1 || el.localName === 'html' || el.localName === 'body') return null;
    if (!found.has(el)) found.set(el, { sources: [], animation: null });
    const entry = found.get(el);
    if (!entry.sources.includes(source)) entry.sources.push(source);
    return entry;
  };

  // A keyframe as plain JSON: its offset, its easing and every animated property as a string.
  const keyframeJson = (frame) => {
    const out = { offset: frame.computedOffset ?? frame.offset ?? null, easing: String(frame.easing ?? 'linear') };
    for (const [key, value] of Object.entries(frame)) {
      if (!['offset', 'computedOffset', 'easing', 'composite'].includes(key)) out[key] = String(value);
    }
    return out;
  };

  // Name, duration (ms, null when infinite or unknown) and keyframes of one animation.
  const animationJson = (animation) => {
    let name = animation.id || '';
    if (typeof CSSAnimation !== 'undefined' && animation instanceof CSSAnimation) name = animation.animationName;
    else if (typeof CSSTransition !== 'undefined' && animation instanceof CSSTransition) name = 'transition: ' + animation.transitionProperty;
    const effect = animation.effect;
    const timing = effect && effect.getComputedTiming ? effect.getComputedTiming() : {};
    const keyframes = effect && effect.getKeyframes ? effect.getKeyframes().map(keyframeJson) : [];
    return { name: name || '(unnamed Web Animation)', duration: Number.isFinite(timing.duration) ? timing.duration : null, keyframes };
  };

  // (1) Every animation's target element; the first animation found on an element is the one reported.
  for (const { root } of scopes) {
    for (const animation of root.getAnimations()) {
      const entry = add(animation.effect && animation.effect.target, 'animation');
      if (entry && !entry.animation) entry.animation = animationJson(animation);
    }
  }

  // (2) A grid x grid set of points spread evenly inside the window; every element stacked at a point.
  const topRoots = scopes.filter((scope) => scope.win === window).map((scope) => scope.root);
  for (let i = 0; i < grid; i++) {
    for (let j = 0; j < grid; j++) {
      const x = rect.x + (i + 0.5) * rect.w / grid;
      const y = rect.y + (j + 0.5) * rect.h / grid;
      for (const root of topRoots) for (const el of root.elementsFromPoint(x, y)) add(el, 'point');
    }
  }

  // (3) Media and frames anywhere in the page (Python keeps an <img> found only here if it is animated).
  for (const { root } of scopes) {
    for (const el of root.querySelectorAll('video, canvas, img, iframe')) add(el, 'media');
  }

  // A selector that names el alone (SPEC 7), in the form ftQueryDeep reads.
  // Inside el's own document or shadow root: '#id' when that id is unique there, otherwise a path of
  // 'tag' or 'tag:nth-of-type(k)' steps (k only when the parent has several children with that tag)
  // from the nearest ancestor with a unique id, or from the top (<html> in a document). In a shadow
  // root the top has no element to name, so ':host > ' anchors the path when it would match more.
  const uniqueId = (root, node) => {
    if (!node.id) return false;
    try { return root.querySelectorAll('#' + CSS.escape(node.id)).length === 1; } catch (e) { return false; }
  };
  const localPath = (el, root) => {
    const steps = [];
    for (let node = el; node && node.nodeType === 1; node = node.parentElement) {
      if (uniqueId(root, node)) { steps.unshift('#' + CSS.escape(node.id)); break; }
      const siblings = node.parentNode ? Array.from(node.parentNode.children) : [node];
      const same = siblings.filter((c) => c.localName === node.localName);
      steps.unshift(same.length > 1 ? `${node.localName}:nth-of-type(${same.indexOf(node) + 1})` : node.localName);
    }
    const path = steps.join(' > ');
    return root.nodeType === 11 && root.querySelectorAll(path).length !== 1 ? ':host > ' + path : path;
  };
  // An element inside a shadow root or a same-origin frame cannot be reached by a selector of the top
  // document, and a path inside one root could also match elements of another. So its host's (or its
  // frame element's) selector goes in front, joined by ' >>> ': 'live-banner >>> div'.
  const frameElementOf = (doc) => {
    try { return doc.defaultView ? doc.defaultView.frameElement : null; } catch (e) { return null; }
  };
  const selectorFor = (el) => {
    const root = el.getRootNode();
    const outer = root.nodeType === 11 ? root.host : (root.nodeType === 9 ? frameElementOf(root) : null);
    const path = localPath(el, root);
    return outer ? selectorFor(outer) + ' >>> ' + path : path;
  };
  // How many elements the selector names in the whole page (SPEC 7 asks for exactly one).
  const matchCount = (selector) => {
    try { return ftQueryDeep(selector, scopes).length; } catch (e) { return 0; }
  };

  // The element's box in top-window CSS px: a box inside a same-origin frame is shifted by the frame's
  // position and border.
  const viewportRect = (el) => {
    const box = el.getBoundingClientRect();
    let x = box.x, y = box.y;
    for (let win = el.ownerDocument.defaultView; win && win !== window && win.frameElement; ) {
      const frame = win.frameElement;
      const frameBox = frame.getBoundingClientRect();
      x += frameBox.x + frame.clientLeft;
      y += frameBox.y + frame.clientTop;
      win = frame.ownerDocument.defaultView;
    }
    return { x, y, w: box.width, h: box.height };
  };

  const crossOrigin = (el) => {
    if (el.localName !== 'iframe') return false;
    try { return !el.contentDocument; } catch (e) { return true; }
  };

  // Tag in document order. An element tagged by an earlier collection in this page keeps its id.
  const elements = Array.from(found.keys()).sort((a, b) => (order.get(a) ?? 1e9) - (order.get(b) ?? 1e9));
  if (!window.__ftNextCandidateId) window.__ftNextCandidateId = 1;
  return elements.map((el) => {
    if (!el.hasAttribute('data-ft-id')) el.setAttribute('data-ft-id', 'ft-' + window.__ftNextCandidateId++);
    const selector = selectorFor(el);
    const entry = found.get(el);
    return {
      order: order.get(el) ?? -1, ftId: el.getAttribute('data-ft-id'), selector, matches: matchCount(selector),
      tag: el.localName, rect: viewportRect(el), sources: entry.sources, animation: entry.animation,
      crossOrigin: crossOrigin(el),
      imageUrl: el.localName === 'img' ? new URL(el.currentSrc || el.getAttribute('src') || '', el.baseURI).href : null,
    };
  });
"""

COLLECT_CANDIDATES_JS = "({ rect, grid }) => {\n" + PAGE_HELPERS_JS + _COLLECT_BODY + "}\n"


@dataclass(frozen=True)
class InspectTarget:
    """One place to look: a frame index and the violation window (x, y, w, h in CSS px) at that frame."""

    frame: int
    window: tuple[float, float, float, float]


@dataclass
class Candidate:
    """One element that could cause a flash, as the page described it at the inspected frame."""

    order: int                        # position in document order (ties in the ranking go to the lower)
    ft_id: str                        # 'ft-<n>', the data-ft-id the inspection page gave it
    selector: str                     # CSS selector meant to match this element alone
    selector_matches: int             # how many elements that selector matched in the inspection page
    node_type: str                    # tag name, or CROSS_ORIGIN_NODE_TYPE
    rect: dict[str, float]            # bounding box {x, y, w, h} in viewport CSS px
    animation: dict[str, Any] | None  # {name, duration, keyframes} of its first animation, if any
    sources: list[str]                # why it is a candidate: 'animation', 'point', 'media'
    animated_image: bool = False      # an <img> whose bytes are an animated GIF/APNG/WebP (detect.py)

    @property
    def can_flash_itself(self) -> bool:
        """True when the element has its own motion: an animation, or media that paints changing pixels."""
        return self.animation is not None or self.animated_image or self.node_type in SELF_PAINTING_TAGS


@dataclass
class Inspection:
    """Candidates per target (same order as the targets) and notes about how they were collected."""

    candidates: list[list[Candidate]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _candidate_from_page(raw: dict[str, Any], animated_image: bool) -> Candidate:
    """Turn one object returned by COLLECT_CANDIDATES_JS into a Candidate."""
    box = raw["rect"]
    return Candidate(
        order=int(raw["order"]),
        ft_id=str(raw["ftId"]),
        selector=str(raw["selector"]),
        selector_matches=int(raw["matches"]),
        node_type=CROSS_ORIGIN_NODE_TYPE if raw["crossOrigin"] else str(raw["tag"]),
        rect={key: float(box[key]) for key in ("x", "y", "w", "h")},
        animation=raw["animation"],
        sources=[str(s) for s in raw["sources"]],
        animated_image=animated_image,
    )


class InspectStrategy(WaapiStepStrategy):
    """Strategy A that stops at the target frames to collect candidates instead of taking screenshots.

    The frame loop is the one of ``WaapiStepStrategy.run``: per frame, advance the fake clock to the frame
    time, then seek every animation, SVG timeline and video. So at frame ``i`` the page shows exactly what
    frame ``i`` of a strategy A capture shows.

    ``lead_in_ms`` is page time to walk through before the interaction script and frame 0, in the same
    16 ms steps as the script's pump (each step fires timers and seeks every animation). Strategy B
    pre-rolls the page to a fixed time before its frame 0 (SPEC 6.2, ADR-023); walking the same time
    puts inspection frame ``i`` at the page time of B's frame ``i``. ``cancel_check`` (optional) is
    called before every step and frame and stops the walk by raising.
    """

    def __init__(self, targets: list[InspectTarget], lead_in_ms: int = 0,
                 cancel_check: Callable[[], None] | None = None) -> None:
        super().__init__()
        self.targets = targets
        self.lead_in_ms = int(lead_in_ms)
        self.cancel_check = cancel_check
        self._led_in = False
        self.collected: dict[int, list[Candidate]] = {}  # target position -> candidates
        self.media_notes: list[str] = []

    def _check_cancel(self) -> None:
        if self.cancel_check is not None:
            self.cancel_check()

    def _walk_lead_in(self, page: Page) -> None:
        """Advance page time from load to ``lead_in_ms``, seeking at every step (once per page load)."""
        if self._led_in:
            return
        self._led_in = True
        walked = 0
        while True:
            self._check_cancel()
            self._tally.add_preroll(*self._seek_pass(page, walked, "pre-roll", walk=walked == 0))
            if walked >= self.lead_in_ms:
                break
            step = min(INTERACTION_PUMP_MS, self.lead_in_ms - walked)
            self._advance_clock(step)
            walked += step
        self._preroll_ms = walked

    def run_interaction(self, page: Page, script: str) -> list[str]:
        """Strategy B runs the script after its pre-roll, so the lead-in is walked first."""
        if self.lead_in_ms:
            self._walk_lead_in(page)
        return super().run_interaction(page, script)

    def run(self, page: Page, settings: CaptureSettings, sink: FrameSink,
            progress: ProgressCallback | None) -> StrategyOutcome:
        if self.lead_in_ms:
            self._walk_lead_in(page)  # does nothing when the interaction script already walked it
        last = max(target.frame for target in self.targets)
        clock_ms = 0
        for index in range(last + 1):
            self._check_cancel()
            target_ms = int(round(settings.frame_time_ms(index)))
            self._advance_clock(target_ms - clock_ms)  # timers and rAF callbacks fire, as in a capture
            clock_ms = target_ms
            pass_ms = self._preroll_ms + settings.frame_time_ms(index)
            stats, abandoned = self._seek_pass(page, pass_ms, "frame0" if index == 0 else "later", walk=index == 0)
            self._tally.add(index, stats, abandoned)
            for position, target in enumerate(self.targets):
                if target.frame == index:
                    self.collected[position] = self._collect(page, target, settings)
        return self._outcome([], settings.fps)

    def _collect(self, page: Page, target: InspectTarget, settings: CaptureSettings) -> list[Candidate]:
        """Ask the page for its candidates at this moment; keep an <img> found only as media if animated."""
        x, y, w, h = target.window
        raw: list[dict[str, Any]] = page.evaluate(COLLECT_CANDIDATES_JS, {"rect": {"x": x, "y": y, "w": w, "h": h},
                                                                          "grid": POINT_GRID})
        local_ok = local_reads_allowed(page.url, settings.allow_local_files)
        candidates = []
        for item in raw:
            animated = item["tag"] == "img" and self._is_animated_image(page, item["imageUrl"] or "", local_ok)
            if item["tag"] == "img" and item["sources"] == ["media"] and not animated:
                continue  # a still image somewhere on the page cannot flash by itself
            candidates.append(_candidate_from_page(item, animated))
        return candidates

    def _is_animated_image(self, page: Page, url: str, local_ok: bool) -> bool:
        """True when detect.py recognises the image's bytes as an animation (GIF, APNG, WebP)."""
        entry, note = _classify_image(page, {"raw": url, "resolved": url}, local_ok)
        if note and note not in self.media_notes:
            self.media_notes.append(note)
        return entry is not None


def collect_candidates(url: str, settings: CaptureSettings, targets: list[InspectTarget],
                       browser: Browser, scratch_dir: Path, *, lead_in_ms: int = 0,
                       cancel_check: Callable[[], None] | None = None) -> Inspection:
    """Load ``url`` fresh with the capture's own set-up and collect candidates at every target frame.

    ``settings`` are the audit's capture settings (viewport, fps, interaction script); ``scratch_dir``
    is an empty folder the capturer's set-up may create and that is deleted afterwards (no frame is
    written there). ``lead_in_ms`` and ``cancel_check`` are passed to ``InspectStrategy``. Returns one
    candidate list per target, in the order of ``targets``.
    """
    if not targets:
        return Inspection()
    page_settings = CaptureSettings(url=resolve_url(url, settings.allow_local_files), duration_s=settings.duration_s,
                                    viewport=settings.viewport, fps=settings.fps,
                                    interaction_script=settings.interaction_script, forced=True,
                                    allow_local_files=settings.allow_local_files)
    strategy = InspectStrategy(targets, lead_in_ms=lead_in_ms, cancel_check=cancel_check)
    attempt = _Attempt(name=strategy.name, strategy=strategy, may_fall_through=False)
    try:
        _run_attempt(browser, attempt, page_settings, scratch_dir, None)
    finally:
        shutil.rmtree(scratch_dir, ignore_errors=True)
    notes = list(attempt.notes) + (list(attempt.outcome.notes) if attempt.outcome else []) + strategy.media_notes
    lists = [strategy.collected.get(position, []) for position in range(len(targets))]
    if len(strategy.collected) < len(targets):
        notes.append(f"inspection reached {len(strategy.collected)} of {len(targets)} target frame(s)")
    return Inspection(candidates=lists, notes=notes)
