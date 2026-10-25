"""JavaScript that FlashTrace runs inside the captured page (SPEC.md section 6.2, ADR-013, ADR-022).

Two kinds of script live here.

* **Init scripts.** They are registered on the browser context, so the browser runs them in every
  document and every frame *before* any page script:

  - ``SHADOW_ROOT_RECORDER_JS`` remembers every shadow root the page creates, open or closed.
    ``document.getAnimations()`` does not look inside shadow roots, and a closed root cannot be reached
    from its host afterwards, so the root has to be caught at the moment it is made (ADR-022).
  - ``SEEDED_RANDOM_JS`` replaces ``Math.random`` with a fixed-seed generator, so a page that picks
    random colours or delays draws the same frames in every capture (ADR-022). ``crypto.getRandomValues``
    is left alone (a documented limitation).
  - ``TIMELINE_CLOCK_JS`` (strategy A only) makes ``document.timeline.currentTime`` read the fake clock,
    so a script that times itself from the animation timeline follows virtual time like one that reads
    ``performance.now()``.
  - ``ANIMATION_RECORDER_JS`` (strategy A only) records what the page itself asks of its animations and
    SVG timelines (pause, play, a new current time), so strategy A can honour it while it drives them.

* **Page helpers.** ``PAGE_HELPERS_JS`` is a block of small JavaScript functions that is pasted at the
  top of every function FlashTrace evaluates in the page (strategy A's per-frame seek, detection's scan
  and the ablation set-up), so all three walk the page in exactly the same way:

  - ``ftScopes`` lists every place animations can live: each same-origin window's document (frames,
    ``<object>`` and ``<embed>`` documents included) plus every shadow root;
  - ``ftOwnCall`` marks a call as FlashTrace's own, so the recorder does not take it for the page's;
  - ``ftQueryDeep`` finds the elements a FlashTrace selector names, including ``host >>> inner``
    selectors for elements inside shadow roots and same-origin frames (``capture/inspect.py``);
  - ``ftIsSuppressed`` says whether an element is itself suppressed for an ablation, and ``ftIsHidden``
    whether a node cannot be seen because of one;
  - ``ftApplySuppression`` performs the ablation set-up of ADR-013.

Suppression stops only what the suppressed element itself does: its own animations, its own picture (a
media element) or its own paint (an element that only page script changes). Its children and the inside
of its shadow root keep running, because they are candidates of their own: a wrapper around a flashing
element must not get the credit for that element's flashes (ADR-013, SPEC section 7).

Nothing here reads the real clock or the network (``TIMELINE_CLOCK_JS`` reads the fake one); every helper
gives the same answer for the same page.
"""

from __future__ import annotations

#: Seed of the ``Math.random`` replacement (ADR-022). Any fixed number works; this one is recorded in
#: the capture notes so a reader can reproduce the sequence.
RANDOM_SEED = 0x2F6E2B1

#: Attribute put on every element that matches a suppressed selector (ADR-013).
SUPPRESSED_ATTRIBUTE = "data-ft-suppressed"

#: Attribute put on a suppressed media element (video, canvas, img, svg, iframe): it is hidden whole.
SUPPRESSED_MEDIA_ATTRIBUTE = "data-ft-suppressed-media"

#: Attribute put on a suppressed element that has no animation of its own: its own paint is hidden, its
#: children stay visible.
SUPPRESSED_PAINT_ATTRIBUTE = "data-ft-suppressed-paint"

#: Separator of a selector that steps inside a shadow root or a same-origin frame: ``host >>> inner``.
DEEP_SELECTOR_SEPARATOR = " >>> "

SHADOW_ROOT_RECORDER_JS = r"""
(() => {
  if (typeof Element === 'undefined' || !Element.prototype.attachShadow) return;
  // The list lives on window but is not enumerable, so page code that lists globals does not see it.
  const roots = [];
  Object.defineProperty(window, '__ftShadowRoots', { value: roots, configurable: true });
  const originalAttachShadow = Element.prototype.attachShadow;
  Element.prototype.attachShadow = function attachShadow(init) {
    const root = originalAttachShadow.call(this, init);  // throws exactly as before for invalid hosts
    roots.push(root);
    return root;
  };
})();
"""

# mulberry32: a 32-bit generator whose whole state is one number. Each call adds a constant to the
# state and scrambles it with multiplications and shifts; the same seed gives the same sequence.
_SEEDED_RANDOM_TEMPLATE = r"""
(() => {
  let state = __SEED__;
  Math.random = function random() {
    state = (state + 0x6D2B79F5) | 0;
    let t = Math.imul(state ^ (state >>> 15), 1 | state);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
})();
"""
SEEDED_RANDOM_JS = _SEEDED_RANDOM_TEMPLATE.replace("__SEED__", str(RANDOM_SEED))

#: Note added by every strategy that installs ``SEEDED_RANDOM_JS``.
SEEDED_RANDOM_NOTE = (
    f"Math.random replaced by a fixed-seed generator (mulberry32, seed {RANDOM_SEED:#x}); "
    "crypto.getRandomValues is not seeded"
)

# Playwright's fake clock replaces Date, timers, requestAnimationFrame and performance.now(), but not the
# animation timeline: document.timeline.currentTime keeps following the real clock. A requestAnimationFrame
# loop that computes its state from it would then draw a state that depends on how fast the machine
# captures. This getter answers with performance.now(), which is the fake clock. Scroll-driven timelines
# keep their own answer. Strategy A drives every Animation object itself, so the browser's internal use of
# the timeline does not matter; only what scripts read changes.
TIMELINE_CLOCK_JS = r"""
(() => {
  if (typeof DocumentTimeline === 'undefined') return;
  // Find the prototype that defines currentTime (AnimationTimeline.prototype in Chromium).
  let owner = DocumentTimeline.prototype;
  while (owner && !Object.getOwnPropertyDescriptor(owner, 'currentTime')) owner = Object.getPrototypeOf(owner);
  const descriptor = owner && Object.getOwnPropertyDescriptor(owner, 'currentTime');
  if (!descriptor || typeof descriptor.get !== 'function') return;
  const realTime = descriptor.get;
  Object.defineProperty(owner, 'currentTime', { ...descriptor, get() {
    const real = realTime.call(this);
    if (!(this instanceof DocumentTimeline) || typeof real !== 'number') return real;
    const documentReal = this === document.timeline ? real : realTime.call(document.timeline);
    if (typeof documentReal !== 'number') return real;
    // A DocumentTimeline made with an originTime stays that many milliseconds behind document.timeline.
    return performance.now() - (documentReal - real);
  } });
})();
"""

#: Note added by strategy A, which installs ``TIMELINE_CLOCK_JS``.
TIMELINE_CLOCK_NOTE = "document.timeline.currentTime reads the fake clock (performance.now()), not the real one"

# ADR-029: a browser runs requestAnimationFrame callbacks once per frame it draws. Playwright's fake clock
# runs them every 16 ms instead, which drifts against FlashTrace's 16.667 ms frames and moved the state of
# a rAF-driven page by up to two frames (an exact 3 Hz page measured 4 per second). This init script keeps
# the callbacks in a queue; strategy A runs the queue once per captured frame, at that frame's clock time,
# before it seeks the animations and takes the picture. Callbacks requested while the queue runs wait for
# the next frame, as in a browser.
RAF_SYNC_JS = r"""
(() => {
  if (window.__ftRaf) return;
  const queue = new Map();  // id -> callback, kept in the order they were requested
  let nextId = 1;
  window.requestAnimationFrame = function requestAnimationFrame(callback) {
    const id = nextId++;
    queue.set(id, callback);
    return id;
  };
  window.cancelAnimationFrame = function cancelAnimationFrame(id) { queue.delete(id); };
  window.__ftRaf = {
    flush(timestamp) {
      const due = Array.from(queue.values());
      queue.clear();
      for (const callback of due) {
        try { callback(timestamp); } catch (error) {
          // A browser reports the error and carries on with the other callbacks.
          if (typeof reportError === 'function') reportError(error); else console.error(error);
        }
      }
      return due.length;
    },
  };
})();
"""

#: Note added by strategy A, which installs ``RAF_SYNC_JS``.
RAF_SYNC_NOTE = "requestAnimationFrame callbacks run once per captured frame, at that frame's clock time"

# Strategy A pauses every animation and SVG timeline and seeks it per frame, so the browser's own record
# of what the page asked for is lost: a page's pause() on an animation we already paused changes nothing
# we can see. These wrappers write the page's requests onto the object instead:
#   __ftPageWantsPaused  true after the page's pause() / pauseAnimations(), false after play(),
#                        reverse() or unpauseAnimations();
#   __ftPageSeek         the current time (ms) the page assigned through currentTime or startTime.
# FlashTrace's own calls are made through ftOwnCall (PAGE_HELPERS_JS), which sets __ftOwnCall on the
# object for the duration of the call, so they are not recorded.
ANIMATION_RECORDER_JS = r"""
(() => {
  // Replaces proto[name] by a function that, for the page's calls only, runs note(object) first.
  const wrapMethod = (proto, name, note) => {
    const original = proto && proto[name];
    if (typeof original !== 'function') return;
    Object.defineProperty(proto, name, { configurable: true, writable: true, value: function (...args) {
      if (!this.__ftOwnCall) note(this);
      return original.apply(this, args);
    } });
  };
  // The same for a property setter: note(object, value) runs before the value is stored.
  const wrapSetter = (proto, name, note) => {
    const descriptor = proto && Object.getOwnPropertyDescriptor(proto, name);
    if (!descriptor || typeof descriptor.set !== 'function') return;
    const realSet = descriptor.set;
    Object.defineProperty(proto, name, { ...descriptor, set(value) {
      if (!this.__ftOwnCall) note(this, value);
      realSet.call(this, value);
    } });
  };
  if (typeof Animation !== 'undefined') {
    const proto = Animation.prototype;
    wrapMethod(proto, 'pause', (animation) => { animation.__ftPageWantsPaused = true; });
    wrapMethod(proto, 'play', (animation) => { animation.__ftPageWantsPaused = false; });
    wrapMethod(proto, 'reverse', (animation) => { animation.__ftPageWantsPaused = false; });
    wrapSetter(proto, 'currentTime', (animation, value) => { animation.__ftPageSeek = value; });
    // A start time means current time = (timeline time - start time) x playback rate.
    wrapSetter(proto, 'startTime', (animation, value) => {
      const now = animation.timeline ? animation.timeline.currentTime : null;
      if (typeof value === 'number' && typeof now === 'number') animation.__ftPageSeek = (now - value) * animation.playbackRate;
    });
  }
  if (typeof SVGSVGElement !== 'undefined') {
    wrapMethod(SVGSVGElement.prototype, 'pauseAnimations', (svg) => { svg.__ftPageWantsPaused = true; });
    wrapMethod(SVGSVGElement.prototype, 'unpauseAnimations', (svg) => { svg.__ftPageWantsPaused = false; });
  }
})();
"""

#: Run through the DevTools protocol on a closed shadow root that the HTML parser created (declarative
#: ``<template shadowrootmode="closed">``). Such a root never passes through ``attachShadow``, so the
#: recorder above never saw it; this adds it to the same list. Returns true when it was not listed yet.
RECORD_SHADOW_ROOT_FN = r"""
function () {
  const win = this.ownerDocument && this.ownerDocument.defaultView;
  if (!win) return false;
  if (!Array.isArray(win.__ftShadowRoots)) Object.defineProperty(win, '__ftShadowRoots', { value: [], configurable: true });
  if (win.__ftShadowRoots.includes(this)) return false;
  win.__ftShadowRoots.push(this);
  return true;
}
"""

PAGE_HELPERS_JS = r"""
  // ---- FlashTrace page helpers (capture/page_scripts.py) ----
  // Every place animations can live: the document of each same-origin window, and every shadow root
  // (open or closed) recorded by the init script whose host is still in the page. With
  // findOpenRoots=true the elements are also walked for open shadow roots the recorder could not see
  // (declarative <template shadowrootmode="open">); those are recorded so later calls find them too.
  const ftScopes = (topWindow, findOpenRoots) => {
    const scopes = [];
    const seenRoots = new Set();
    const seenWindows = new Set();
    const recordedRoots = (win) => {
      if (!Array.isArray(win.__ftShadowRoots)) {
        Object.defineProperty(win, '__ftShadowRoots', { value: [], configurable: true });
      }
      return win.__ftShadowRoots;
    };
    const addRoot = (win, root) => {
      if (seenRoots.has(root)) return;
      seenRoots.add(root);
      scopes.push({ win, root });
      if (findOpenRoots) {
        for (const el of root.querySelectorAll('*')) {
          if (!el.shadowRoot) continue;
          if (!recordedRoots(win).includes(el.shadowRoot)) recordedRoots(win).push(el.shadowRoot);
          addRoot(win, el.shadowRoot);
        }
      }
      // Same-origin frames, found through their elements so frames inside shadow roots count too. An
      // <object> or <embed> that shows a document (an SVG file, say) holds a window of its own as well.
      for (const frame of root.querySelectorAll('iframe, frame, object, embed')) {
        let child = null;
        try {
          const doc = frame.contentDocument || (typeof frame.getSVGDocument === 'function' ? frame.getSVGDocument() : null);
          child = doc ? doc.defaultView : null;
        } catch (e) { child = null; }
        if (child) addWindow(child);
      }
    };
    const addWindow = (win) => {
      if (seenWindows.has(win)) return;
      seenWindows.add(win);
      let doc = null;
      try { doc = win.document; } catch (e) { return; }  // cross-origin: detect.py reports such frames
      if (!doc) return;
      addRoot(win, doc);
      for (const root of recordedRoots(win).slice()) {
        if (root.host && root.host.isConnected) addRoot(win, root);
      }
    };
    addWindow(topWindow);
    return scopes;
  };

  // Runs fn with target marked as handled by FlashTrace, so ANIMATION_RECORDER_JS does not mistake our
  // own pause() or currentTime writes for the page's.
  const ftOwnCall = (target, fn) => {
    target.__ftOwnCall = true;
    try { return fn(); } finally { target.__ftOwnCall = false; }
  };

  // True when node is an element suppressed for an ablation (ADR-013). Only the element itself counts:
  // its children and the elements inside its shadow root are candidates of their own, so suppressing
  // a wrapper must not stop what they do.
  const ftIsSuppressed = (node) => Boolean(node) && node.nodeType === 1 && node.hasAttribute('data-ft-suppressed');

  // True when node cannot be seen because of an ablation: its own paint is hidden, or it is (or sits
  // inside) a media element hidden whole, looking through shadow roots (to their host) and same-origin
  // frames (to their <iframe> element). Detection and the media wait skip such nodes.
  const ftIsHidden = (node) => {
    if (node && node.nodeType === 1 && node.hasAttribute('data-ft-suppressed-paint')) return true;
    for (let n = node; n; ) {
      if (n.nodeType === 1 && n.hasAttribute('data-ft-suppressed-media')) return true;
      if (n.parentNode) { n = n.parentNode; continue; }
      if (n.host) { n = n.host; continue; }
      try { n = n.defaultView ? n.defaultView.frameElement : null; } catch (e) { n = null; }
    }
    return false;
  };

  // Every shadow root in scopes (closed ones included), keyed by its host element.
  const ftShadowRootsByHost = (scopes) => {
    const byHost = new Map();
    for (const { root } of scopes) { if (root.host) byHost.set(root.host, root); }
    return byHost;
  };

  // The elements a FlashTrace selector names (capture/inspect.py writes them). A plain CSS selector is
  // matched in the top document. ' >>> ' steps inside an element: the part after it is matched in the
  // shadow root of every element the part before it matched, or in the document of a same-origin
  // frame. So 'live-banner >>> div' is the <div> inside <live-banner>'s shadow root, and the steps can
  // repeat. An invalid CSS part throws a SyntaxError, exactly as querySelectorAll does.
  const ftQueryDeep = (selector, scopes) => {
    const shadowRoots = ftShadowRootsByHost(scopes);
    const inside = (el) => {
      if (shadowRoots.has(el)) return shadowRoots.get(el);
      try {  // a frame element's document; null for any other element and for a cross-origin frame
        return el.contentDocument || (typeof el.getSVGDocument === 'function' ? el.getSVGDocument() : null);
      } catch (e) { return null; }
    };
    let roots = [scopes[0].root];  // ftScopes lists the top document first
    let found = [];
    for (const part of selector.split('>>>').map((text) => text.trim())) {
      found = roots.flatMap((root) => Array.from(root.querySelectorAll(part)));
      roots = found.map(inside).filter(Boolean);
    }
    return found;
  };

  // The outermost <svg> around an SVG element: SMIL timelines belong to that element.
  const ftOutermostSvg = (el) => {
    let svg = el.ownerSVGElement || (el.localName === 'svg' ? el : null);
    while (svg && svg.ownerSVGElement) svg = svg.ownerSVGElement;
    return svg;
  };

  // The ablation's hiding rules, put once into every document or shadow root that needs them.
  //  1. A suppressed media element is hidden whole: everything it shows is its own picture. An element
  //     marked for its own paint is hidden too (its background, border, text and ::before/::after).
  //  2. visibility is inherited, so the children of an element hidden for its own paint get 'visible'
  //     back. :where() gives the rule zero specificity, so a visibility the page sets on a child
  //     itself (a class, an id rule, an inline style) still wins.
  //  3. The same inside a shadow root, for the top-level elements of a host hidden for its own paint.
  const FT_HIDE_CSS = [
    '[data-ft-suppressed-media], [data-ft-suppressed-paint] { visibility: hidden !important; }',
    ':where([data-ft-suppressed-paint]) > * { visibility: visible; }',
    ':where(:host([data-ft-suppressed-paint])) > * { visibility: visible; }',
  ].join('\n');
  const ftAddHideRule = (root) => {
    if (root.__ftHideRule) return;
    const doc = root.nodeType === 9 ? root : root.ownerDocument;
    const style = doc.createElementNS('http://www.w3.org/1999/xhtml', 'style');
    style.textContent = FT_HIDE_CSS;
    (root.nodeType === 9 ? (root.head || root.documentElement) : root).appendChild(style);
    root.__ftHideRule = style;
  };

  // Hides el with one of the two attributes above. The rules go into el's own document or shadow root
  // and, when el hosts a shadow root, into that root as well (rule 3).
  const ftHide = (el, attribute, shadowRoots) => {
    el.setAttribute(attribute, '1');
    ftAddHideRule(el.getRootNode());
    if (shadowRoots.has(el)) ftAddHideRule(shadowRoots.get(el));
  };

  // Strategies B and C apply the ablation once, before frame 0: animations and CSS transitions of a
  // suppressed element (its own, ::before/::after included) are paused where they stand, so they hold
  // their frame-0 state (ADR-013 (a)). Strategy A holds them itself at the exact frame-0 time
  // (waapi_step.py).
  const ftPauseSuppressedAnimations = (root) => {
    let paused = 0;
    for (const animation of root.getAnimations()) {
      const target = animation.effect && animation.effect.target;
      if (!target || !ftIsSuppressed(target) || animation.playState === 'paused') continue;
      ftOwnCall(animation, () => animation.pause());
      paused++;
    }
    return paused;
  };

  const FT_MEDIA = 'video, canvas, img, svg, iframe';

  // True when el has an animation of its own that the capturer can hold: a Web Animation, CSS animation
  // or transition whose target is el (on el itself or on its ::before/::after box), or a SMIL element
  // aimed at el. Animations of its children do not count: they belong to the children. An element
  // without one is changed only by page script, so hiding its paint is the only way to suppress it
  // (ADR-013 (b)).
  const ftHasOwnAnimation = (el) => {
    let animations = [];
    try { animations = el.getAnimations({ subtree: true }); } catch (e) { return true; }  // unknown: do not hide
    if (animations.some((animation) => animation.effect && animation.effect.target === el)) return true;
    for (const smil of el.getRootNode().querySelectorAll('animate, set, animateTransform, animateMotion, animateColor')) {
      if (smil.targetElement === el) return true;
    }
    return false;
  };

  // A suppressed outermost <svg> keeps its SMIL timeline paused at time 0.
  const ftHoldSuppressedSvgs = (root) => {
    let held = 0;
    for (const svg of root.querySelectorAll('svg')) {
      if (svg.ownerSVGElement !== null || typeof svg.pauseAnimations !== 'function' || !ftIsSuppressed(svg)) continue;
      if (svg.__ftSmilHeld && svg.animationsPaused()) continue;
      if (!svg.__ftSmilHeld) held++;
      svg.__ftSmilHeld = true;
      svg.pauseAnimations();
      svg.setCurrentTime(0);
    }
    return held;
  };

  // A SMIL element (<animate> and friends) that animates a suppressed element inside an <svg> that is
  // not held itself is removed, which returns the animated attribute to its base value.
  const ftRemoveSuppressedSmil = (root) => {
    let removed = 0;
    for (const el of root.querySelectorAll('animate, set, animateTransform, animateMotion, animateColor')) {
      const target = el.targetElement;
      const svg = ftOutermostSvg(el);
      if (!target || !ftIsSuppressed(target) || (svg && svg.__ftSmilHeld)) continue;
      el.remove();
      removed++;
    }
    return removed;
  };

  // Marks every element a suppressed selector names (ftQueryDeep) with data-ft-suppressed, then hides
  // what the capturer cannot hold (ADR-013 (b)): a media element whole, and an element without an
  // animation of its own for its own paint only, so its children stay visible. Fills counts.
  const ftMarkSuppressed = (selectors, scopes, counts) => {
    const shadowRoots = ftShadowRootsByHost(scopes);
    for (const selector of selectors) {
      let elements = [];
      try { elements = ftQueryDeep(selector, scopes); } catch (e) {
        if (!counts.invalidSelectors.includes(selector)) counts.invalidSelectors.push(selector);
        continue;
      }
      if (!elements.length && !counts.unmatchedSelectors.includes(selector)) counts.unmatchedSelectors.push(selector);
      for (const el of elements) {
        if (ftIsSuppressed(el)) continue;  // marked by an earlier pass or an earlier selector
        el.setAttribute('data-ft-suppressed', '1');
        counts.elements++;
        if (el.matches(FT_MEDIA)) {
          ftHide(el, 'data-ft-suppressed-media', shadowRoots);
          counts.media++;
        } else if (!ftHasOwnAnimation(el)) {
          ftHide(el, 'data-ft-suppressed-paint', shadowRoots);
          counts.scripted++;
        }
      }
    }
  };

  // Ablation set-up (ADR-013): mark every element a suppressed selector names, then stop what the
  // element itself animates. pauseAnimations: true when the set-up runs only once (strategies B and
  // C); strategy A passes false and holds the animations itself. Safe to call again: only new work is
  // counted.
  const ftApplySuppression = (selectors, scopes, pauseAnimations) => {
    const counts = { elements: 0, animations: 0, media: 0, scripted: 0, svgTimelines: 0, smilElements: 0,
                     invalidSelectors: [], unmatchedSelectors: [] };
    ftMarkSuppressed(selectors, scopes, counts);
    for (const { root } of scopes) {
      if (pauseAnimations) counts.animations += ftPauseSuppressedAnimations(root);
      counts.svgTimelines += ftHoldSuppressedSvgs(root);
      counts.smilElements += ftRemoveSuppressedSmil(root);
    }
    return counts;
  };
  // ---- end of page helpers ----
"""

#: Ablation set-up, evaluated once after the page has settled and before detection and frame 0.
#: Takes ``{selectors, pauseAnimations}``; returns the counts ``ftApplySuppression`` collected.
APPLY_SUPPRESSION_JS = (
    "({ selectors, pauseAnimations }) => {\n"
    + PAGE_HELPERS_JS
    + "\n  return ftApplySuppression(selectors, ftScopes(window, true), pauseAnimations);\n}"
)
