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
  - ``ANIMATION_RECORDER_JS`` (strategy A only) records what the page itself asks of its animations, SVG
    timelines and media elements (pause, play, a new current time), so strategy A can honour it while it
    drives them.
  - ``RAF_SYNC_JS`` (strategy A only) queues ``requestAnimationFrame`` callbacks so strategy A runs them
    once per captured frame (ADR-029).
  - ``IO_SYNC_JS`` (strategy A only) replaces ``IntersectionObserver`` with one that strategy A evaluates
    once per captured frame, on that frame's layout, and whose callbacks run in a task on the page clock
    (ADR-052).

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

# ADR-052: Chromium computes IntersectionObserver entries in its own rendering updates, which follow the
# real clock, and delivers them in a task after that update. Under strategy A a reveal-on-scroll therefore
# started one frame earlier or later between two captures of the same page (the UI self-audit's E5 differed
# in 23 frames). This init script replaces IntersectionObserver (and IntersectionObserverEntry, so instanceof
# and feature checks keep working) with an implementation that the seek pass evaluates once per captured
# frame, on the layout of that frame's picture (after the animations are seeked), exactly as a browser
# evaluates once per rendering update. The entries are delivered as a browser does, in a task after the
# picture: a 0 ms timer on the page clock (the fake setTimeout, saved here), which fires at the start of the
# next clock step, after any 0 ms timers the page set during that same pass (the fake clock fires timers due
# at the same time in the order they were set).
#
# What is implemented: the constructor (callback, {root, rootMargin, threshold, scrollMargin, delay,
# trackVisibility}) with Chromium's validation and error texts, observe / unobserve / disconnect /
# takeRecords, the root, rootMargin, scrollMargin, thresholds, delay and trackVisibility attributes, and
# entries with time (performance.now(), the page clock), rootBounds, boundingClientRect, intersectionRect,
# isIntersecting, isVisible, intersectionRatio and target. The root is an element, or the viewport of a
# document (an explicit Document, or the top-level document for root null). rootMargin is one to four
# lengths in px or % (top right bottom left; % of the root's height for top and bottom, of its width for
# left and right). thresholds is a number or a list (default [0]), stored as float32 and sorted, as Chromium
# does, and intersectionRatio is computed in float32 as Chromium computes it (areas and quotient), so a ratio
# exactly on a threshold such as 0.1 reaches it, as in Chrome. An entry is queued for each target at the first evaluation after observe(), then whenever the
# crossed-threshold index changes (how many thresholds the ratio has reached; 0 when the boxes do not even
# touch). As Chromium does (measured against its own observer), isIntersecting is "the index is above 0":
# under threshold 0.5 a target 40 % in view reports isIntersecting false with its ratio and intersectionRect,
# and one that enters to 20 % gets no entry. The specification's isIntersecting is the bare geometric test and
# also queues an entry when only that changes; this follows the browser being captured. A target without a box
# (not connected, display: none or contents), or outside an explicit root's document or containing block
# chain, does not intersect and has empty rectangles, rootBounds included (measured). Entries of different
# observers are delivered in the order the observers were created; Chromium's own order follows an internal
# hash set (measured: neither creation order nor its reverse), so no page can rely on it.
#
# Simplifications (geometry from getBoundingClientRect): the target's box is clipped by the overflow clip
# (overflow other than visible on that axis, or contain: paint) of each ancestor in its containing block
# chain, approximated from the flat tree and CSS position (an absolutely positioned element skips static
# ancestors, a fixed one every ancestor without a transform, filter or containment), using each ancestor's
# padding box as getBoundingClientRect, borders and scrollbars give it. Transforms, clip-path, clip,
# border-radius, overflow-clip-margin and scrollMargin are not applied; isVisible (trackVisibility) is
# isIntersecting rather than an occlusion test. Same-origin frames get their own copy, and their targets are
# clipped by their frame's viewport and mapped through the frame element's content box on the way to the
# implicit root. A frame whose top-level document is cross-origin keeps the browser's own observer: strategy
# A cannot reach it (such frames are uncontrollable content anyway). Callbacks are invoked with (entries,
# observer) and observer as this; an error a callback throws is reported like RAF_SYNC_JS reports it and the
# other callbacks still run.
IO_SYNC_JS = r"""
(() => {
  if (window.__ftIo || typeof window.IntersectionObserver !== 'function') return;
  try { void window.top.document; } catch (e) { return; }  // the top-level document is cross-origin
  const queueTask = window.setTimeout;  // the page clock's setTimeout: the fake clock is installed first
  const Rect = window.DOMRectReadOnly;
  const report = (error) => { if (typeof reportError === 'function') reportError(error); else console.error(error); };
  const internals = new WeakMap();  // observer -> its state
  const entryData = new WeakMap();  // entry -> its fields
  const active = new Set();         // observers that observe at least one target
  let nextId = 1;
  let taskQueued = false;

  const isElement = (v) => v !== null && typeof v === 'object' && v.nodeType === 1 && typeof v.getBoundingClientRect === 'function';
  const isDocument = (v) => v !== null && typeof v === 'object' && v.nodeType === 9 && typeof v.createElement === 'function';
  const fail = (what, message) => `Failed to ${what}: ${message}`;
  const CONSTRUCT = "construct 'IntersectionObserver'";

  // rootMargin and scrollMargin: one to four lengths in px or %, top right bottom left as in CSS margins.
  // As in Chromium (measured), px lengths are floored to whole pixels when parsed ('10.7px' reads back as
  // '10px', '-3.6px' as '-4px'), and a percentage is kept as given and resolved later, truncated toward 0.
  // The value is CSS, so comments separate lengths like white space ('10px/**/20px' is two lengths); one
  // left open runs to the end, as in the CSS tokenizer.
  const parseMargin = (value, name) => {
    const text = String(value).replace(/\/\*[\s\S]*?(?:\*\/|$)/g, ' ').trim();
    const tokens = text === '' ? [] : text.split(/\s+/);
    if (tokens.length > 4) throw new DOMException(fail(CONSTRUCT, `Extra text found at the end of ${name}.`), 'SyntaxError');
    const parts = tokens.map((token) => {
      const match = /^([+-]?(?:\d+\.?\d*|\.\d+)(?:e[+-]?\d+)?)(px|%)$/i.exec(token);
      if (!match) throw new DOMException(fail(CONSTRUCT, `${name} must be specified in pixels or percent.`), 'SyntaxError');
      const percent = match[2] === '%';
      return { value: percent ? Number(match[1]) : Math.floor(Number(match[1])), unit: percent ? '%' : 'px' };
    });
    if (!parts.length) parts.push({ value: 0, unit: 'px' });
    const [top, right = top, bottom = top, left = right] = parts;
    return [top, right, bottom, left];
  };
  const marginText = (margin) => margin.map((part) => `${part.value}${part.unit}`).join(' ');

  // threshold: a number or a list of numbers in [0, 1]; float32 like Chromium's, sorted, [0] when empty.
  const parseThresholds = (value) => {
    let list = [0];
    if (value !== undefined) {
      list = value !== null && typeof value === 'object' && typeof value[Symbol.iterator] === 'function' ? Array.from(value) : [value];
    }
    const thresholds = list.map((item) => {
      const number = Number(item);
      if (!Number.isFinite(number)) {
        throw new TypeError(fail(CONSTRUCT, "Failed to read the 'threshold' property from 'IntersectionObserverInit': The provided double value is non-finite."));
      }
      if (number < 0 || number > 1) throw new RangeError(fail(CONSTRUCT, 'Threshold values must be numbers between 0 and 1'));
      return Math.fround(number);
    });
    if (!thresholds.length) thresholds.push(0);
    return thresholds.sort((a, b) => a - b);
  };

  const state = (observer) => {
    const found = internals.get(observer);
    if (!found) throw new TypeError('Illegal invocation');
    return found;
  };

  // Queued entries live with each observed target, as in Chromium: unobserve() and disconnect() drop them.
  const takeQueued = (observerState) => {
    const records = [];
    for (const registration of observerState.targets) records.push(...registration.queued.splice(0));
    return records;
  };

  class IntersectionObserverEntry {
    constructor() { throw new TypeError(fail("construct 'IntersectionObserverEntry'", 'Illegal constructor')); }
  }
  for (const name of ['time', 'rootBounds', 'boundingClientRect', 'intersectionRect', 'isIntersecting', 'isVisible',
                      'intersectionRatio', 'target']) {
    Object.defineProperty(IntersectionObserverEntry.prototype, name, { enumerable: true, configurable: true, get() {
      const data = entryData.get(this);
      if (!data) throw new TypeError('Illegal invocation');
      return data[name];
    } });
  }

  // A function rather than a class, so a call without new throws Chromium's error rather than V8's.
  function IntersectionObserver(callback, options) {
    if (!new.target) {
      throw new TypeError(fail(CONSTRUCT, "Please use the 'new' operator, this DOM object constructor cannot be called as a function."));
    }
    if (typeof callback !== 'function') throw new TypeError(fail(CONSTRUCT, 'parameter 1 is not of type \'Function\'.'));
    if (options !== undefined && options !== null && typeof options !== 'object' && typeof options !== 'function') {
      throw new TypeError(fail(CONSTRUCT, "The provided value is not of type 'IntersectionObserverInit'."));
    }
    const init = options || {};
    // Dictionary members are read in alphabetical order, as WebIDL does.
    const delayValue = init.delay;
    const rootValue = init.root;
    const rootMarginValue = init.rootMargin;
    const scrollMarginValue = init.scrollMargin;
    const thresholdValue = init.threshold;
    const trackVisibility = Boolean(init.trackVisibility);
    const root = rootValue === undefined ? null : rootValue;
    if (root !== null && !isElement(root) && !isDocument(root)) {
      throw new TypeError(fail(CONSTRUCT, "Failed to read the 'root' property from 'IntersectionObserverInit': The provided value is not of type '(Document or Element)'."));
    }
    const delay = delayValue === undefined ? 0 : Number(delayValue);
    if (!Number.isFinite(delay)) {
      throw new TypeError(fail(CONSTRUCT, "Failed to read the 'delay' property from 'IntersectionObserverInit': The provided double value is non-finite."));
    }
    const rootMargin = parseMargin(rootMarginValue === undefined ? '0px' : rootMarginValue, 'rootMargin');
    const scrollMargin = parseMargin(scrollMarginValue === undefined ? '0px' : scrollMarginValue, 'scrollMargin');
    const thresholds = parseThresholds(thresholdValue);
    if (trackVisibility && delay < 100) {
      throw new DOMException(fail(CONSTRUCT, "To enable the 'trackVisibility' option, you must also use a 'delay' option with a value of at least 100. Visibility is more expensive to compute than the basic intersection; enabling this option may negatively affect your page's performance. Please make sure you *really* need visibility tracking before enabling the 'trackVisibility' option."), 'NotSupportedError');
    }
    // The delay attribute reads back as Chromium reports it: whole milliseconds, truncated toward zero, a
    // negative delay included (measured: -5 reads -5, 150.7 reads 150). Throttling treats a negative delay as none.
    internals.set(this, { id: nextId++, callback, root, rootMargin, scrollMargin, thresholds,
                          delayAttribute: Math.trunc(delay) || 0, delay: Math.max(0, delay), trackVisibility,
                          targets: [], lastRun: null });
  }
  // The prototype's members, defined as a class would define them (methods are not constructors).
  const members = {
    get root() { return state(this).root; },
    get rootMargin() { return marginText(state(this).rootMargin); },
    get scrollMargin() { return marginText(state(this).scrollMargin); },
    get thresholds() { return Object.freeze(state(this).thresholds.slice()); },  // a new frozen array each time, as Chromium
    get delay() { return state(this).delayAttribute; },
    get trackVisibility() { return state(this).trackVisibility; },
    // Operations in alphabetical order, as Chromium enumerates them.
    disconnect() {
      state(this).targets = [];
      active.delete(this);
    },
    observe(target) {
      const own = state(this);
      if (!isElement(target)) throw new TypeError(fail("execute 'observe' on 'IntersectionObserver'", "parameter 1 is not of type 'Element'."));
      if (own.targets.some((registration) => registration.target === target)) return;
      // previousThresholdIndex -1: the first evaluation always queues an entry.
      own.targets.push({ target, previousThresholdIndex: -1, previousIsVisible: false, queued: [] });
      active.add(this);
    },
    takeRecords() { return takeQueued(state(this)); },
    unobserve(target) {
      const own = state(this);
      if (!isElement(target)) throw new TypeError(fail("execute 'unobserve' on 'IntersectionObserver'", "parameter 1 is not of type 'Element'."));
      own.targets = own.targets.filter((registration) => registration.target !== target);
      if (!own.targets.length) active.delete(this);
    },
  };
  Object.defineProperties(IntersectionObserver.prototype, Object.getOwnPropertyDescriptors(members));
  Object.defineProperty(IntersectionObserver, 'prototype', { writable: false });  // as an interface object's
  // WebIDL members are enumerable; IntersectionObserver takes one required argument, IntersectionObserverEntry none.
  for (const proto of [IntersectionObserver.prototype, IntersectionObserverEntry.prototype]) {
    for (const name of Object.getOwnPropertyNames(proto)) {
      if (name !== 'constructor') Object.defineProperty(proto, name, { enumerable: true });
    }
  }
  Object.defineProperty(IntersectionObserver.prototype, Symbol.toStringTag, { value: 'IntersectionObserver', configurable: true });
  Object.defineProperty(IntersectionObserverEntry.prototype, Symbol.toStringTag, { value: 'IntersectionObserverEntry', configurable: true });
  Object.defineProperty(IntersectionObserver, 'length', { value: 1 });
  Object.defineProperty(IntersectionObserverEntry, 'length', { value: 0 });

  // ---- Geometry. A box is { l, t, r, b } in the viewport coordinates of one document. ----
  const box = (rect) => ({ l: rect.left, t: rect.top, r: rect.right, b: rect.bottom });
  // An area as Chromium computes it (gfx::SizeF::GetArea): width and height as float32, their product in float32.
  const area32 = (b) => (b ? Math.fround(Math.fround(Math.max(0, b.r - b.l)) * Math.fround(Math.max(0, b.b - b.t))) : 0);
  const shift = (b, dx, dy) => (b ? { l: b.l + dx, t: b.t + dy, r: b.r + dx, b: b.b + dy } : null);
  // Edge-inclusive intersection on the chosen axes: boxes that only touch still intersect (zero area).
  const clip = (b, by, x = true, y = true) => {
    if (!b) return null;
    const out = { l: x ? Math.max(b.l, by.l) : b.l, t: y ? Math.max(b.t, by.t) : b.t,
                  r: x ? Math.min(b.r, by.r) : b.r, b: y ? Math.min(b.b, by.b) : b.b };
    return out.r < out.l || out.b < out.t ? null : out;
  };
  const toRect = (b) => (b ? new Rect(b.l, b.t, b.r - b.l, b.b - b.t) : new Rect(0, 0, 0, 0));
  const px = (value) => parseFloat(value) || 0;

  // One evaluation reads each element's computed style and padding box once.
  const newCache = () => ({ styles: new Map(), paddings: new Map() });
  const styleOf = (el, cache) => {
    let style = cache.styles.get(el);
    if (!style) { style = el.ownerDocument.defaultView.getComputedStyle(el); cache.styles.set(el, style); }
    return style;
  };
  // The padding box: the border box less borders and scrollbars (the area an overflow clip keeps).
  const paddingBox = (el, cache) => {
    let found = cache.paddings.get(el);
    if (found) return found;
    const s = styleOf(el, cache);
    const outer = box(el.getBoundingClientRect());
    const bl = px(s.borderLeftWidth), br = px(s.borderRightWidth), bt = px(s.borderTopWidth), bb = px(s.borderBottomWidth);
    const barY = typeof el.offsetWidth === 'number' ? Math.max(0, el.offsetWidth - el.clientWidth - Math.round(bl) - Math.round(br)) : 0;
    const barX = typeof el.offsetHeight === 'number' ? Math.max(0, el.offsetHeight - el.clientHeight - Math.round(bt) - Math.round(bb)) : 0;
    found = { l: outer.l + bl, t: outer.t + bt, r: Math.max(outer.l + bl, outer.r - br - barY), b: Math.max(outer.t + bt, outer.b - bb - barX) };
    cache.paddings.set(el, found);
    return found;
  };
  const paintContained = (s) => /paint|strict|content/.test(s.contain || '');
  // Whether el clips its overflow on each axis. The root element's overflow, and the body's when the root
  // element's is visible, belong to the viewport.
  const overflowClip = (el, cache) => {
    const doc = el.ownerDocument;
    if (el === doc.documentElement) return null;
    const s = styleOf(el, cache);
    if (el === doc.body && doc.documentElement) {
      const rootStyle = styleOf(doc.documentElement, cache);
      if (rootStyle.overflowX === 'visible' && rootStyle.overflowY === 'visible') return null;
    }
    if (s.display === 'contents' || s.display === 'inline') return null;
    const paint = paintContained(s);
    const x = paint || s.overflowX !== 'visible';
    const y = paint || s.overflowY !== 'visible';
    return x || y ? { x, y } : null;
  };
  const none = (value) => !value || value === 'none' || value === 'normal' || value === 'auto';
  // An ancestor that contains position: fixed descendants (a transform, filter or containment).
  const containsFixed = (s) => !none(s.transform) || !none(s.perspective) || !none(s.filter) || !none(s.backdropFilter)
    || !none(s.translate) || !none(s.rotate) || !none(s.scale) || /paint|layout|strict|content/.test(s.contain || '')
    || /transform|perspective|filter/.test(s.willChange || '') || (s.containerType && s.containerType !== 'normal');
  const modeOf = (s) => (s.position === 'fixed' ? 'fixed' : s.position === 'absolute' ? 'absolute' : 'flow');
  // The parent in the flat tree: a slotted element's slot, a shadow root's host.
  const parentOf = (node) => node.assignedSlot || node.parentElement || (node.parentNode && node.parentNode.host) || null;

  // Clips b by the overflow clip of every ancestor of el in its containing block chain inside el's document,
  // up to stop (not included). Returns { b, reached }: reached tells whether stop is in that chain.
  const clipByAncestors = (el, b, stop, cache) => {
    let mode = modeOf(styleOf(el, cache));
    for (let a = parentOf(el); a && a.nodeType === 1; a = parentOf(a)) {
      const s = styleOf(a, cache);
      const contains = mode === 'flow' || containsFixed(s) || (mode === 'absolute' && s.position !== 'static');
      if (a === stop) return { b, reached: contains };
      if (!contains) continue;
      const axes = overflowClip(a, cache);
      if (axes) b = clip(b, paddingBox(a, cache), axes.x, axes.y);
      mode = modeOf(s);
    }
    return { b, reached: false };
  };

  const viewportOf = (doc) => {
    const win = doc.defaultView;
    const standards = doc.compatMode === 'CSS1Compat' && doc.documentElement;
    return { l: 0, t: 0, r: standards ? doc.documentElement.clientWidth : win.innerWidth,
             b: standards ? doc.documentElement.clientHeight : win.innerHeight };
  };
  // The root intersection rectangle grown by rootMargin (% of its height for top and bottom, of its width
  // for left and right, in whole pixels), in the root document's viewport coordinates.
  const rootRect = (own, rootDoc, cache) => {
    const root = own.root;
    let b;
    if (isElement(root)) b = overflowClip(root, cache) || paintContained(styleOf(root, cache)) ? paddingBox(root, cache) : box(root.getBoundingClientRect());
    else b = viewportOf(rootDoc);
    const [top, right, bottom, left] = own.rootMargin;
    const width = b.r - b.l, height = b.b - b.t;
    const length = (part, base) => (part.unit === '%' ? Math.trunc((part.value * base) / 100) : part.value);
    return { l: b.l - length(left, width), t: b.t - length(top, height), r: b.r + length(right, width), b: b.b + length(bottom, height) };
  };

  // The intersection of target with the observer's root (SPEC 'compute the intersection', simplified as the
  // comment above this script says).
  const geometry = (target, own, cache) => {
    const empty = { rootBounds: new Rect(0, 0, 0, 0), target: null, inter: null, intersects: false };
    const doc = target.ownerDocument;
    if (!target.isConnected || !doc || !doc.defaultView || target.getClientRects().length === 0) return empty;
    const root = own.root;
    if (isElement(root) && (!root.isConnected || root.getClientRects().length === 0)) return empty;
    const targetBox = box(target.getBoundingClientRect());
    const outcome = (rootBounds, inter, intersecting) => ({ rootBounds: toRect(rootBounds), target: targetBox,
                                                           inter: intersecting ? inter : null, intersects: intersecting });
    if (root !== null) {
      // An explicit root: a target outside its document or its containing block chain does not intersect,
      // with every rectangle empty (measured).
      const rootDoc = isDocument(root) ? root : root.ownerDocument;
      if (rootDoc !== doc) return empty;
      const walked = clipByAncestors(target, targetBox, isElement(root) ? root : null, cache);
      if (isElement(root) && !walked.reached) return empty;
      const bounds = rootRect(own, rootDoc, cache);
      const inter = clip(walked.b, bounds);
      return outcome(bounds, inter, Boolean(inter));
    }
    // The implicit root: the top-level viewport, reached through the same-origin frames around the target.
    let b = targetBox;
    let el = target;
    let dx = 0, dy = 0;  // from the target's viewport coordinates to the current document's
    for (;;) {
      const here = el.ownerDocument;
      b = clipByAncestors(el, b, null, cache).b;
      const frame = here.defaultView.frameElement;
      if (!frame) {
        const bounds = rootRect(own, here, cache);
        const inter = clip(b, bounds);
        return outcome(bounds, shift(inter, -dx, -dy), Boolean(inter));
      }
      b = clip(b, viewportOf(here));
      const s = styleOf(frame, cache);
      const outer = frame.getBoundingClientRect();
      const ox = outer.left + px(s.borderLeftWidth) + px(s.paddingLeft);
      const oy = outer.top + px(s.borderTopWidth) + px(s.paddingTop);
      b = shift(b, ox, oy);
      dx += ox; dy += oy;
      el = frame;
    }
  };

  // Chromium's threshold index: how many thresholds the ratio has reached; 0 when not intersecting.
  const thresholdIndex = (thresholds, ratio) => {
    let index = 0;
    while (index < thresholds.length && thresholds[index] <= ratio) index++;
    return index;
  };

  // intersectionRatio: the share of the target's area that intersects; a target of zero area that
  // intersects has 1. Chromium computes both areas and their quotient in float32 and exposes that float32
  // value, so a ratio exactly on a threshold such as 0.1 (whose float32 lies above the decimal) reaches it:
  // 10 px of a 100 px target in view gives 0.10000000149011612, as in Chromium, not 0.1.
  const ratioOf = (g) => {
    if (!g.intersects) return 0;
    const targetArea = area32(g.target);
    return targetArea > 0 ? Math.min(1, Math.fround(area32(g.inter) / targetArea)) : 1;
  };

  const makeEntry = (time, g, ratio, isIntersecting, isVisible, target) => {
    const entry = Object.create(IntersectionObserverEntry.prototype);
    entryData.set(entry, { time, rootBounds: g.rootBounds, boundingClientRect: toRect(g.target), intersectionRect: toRect(g.inter),
                           isIntersecting, isVisible, intersectionRatio: ratio, target });
    return entry;
  };

  const byId = (a, b) => internals.get(a).id - internals.get(b).id;

  // The intersection observer task: every observer with queued entries, in creation order, gets its callback.
  const notify = () => {
    taskQueued = false;
    for (const observer of Array.from(active).sort(byId)) {
      const own = internals.get(observer);
      const records = takeQueued(own);
      if (!records.length) continue;
      try { own.callback.call(observer, records, observer); } catch (error) { report(error); }
    }
  };

  // Run by strategy A's seek pass once per pass, on the seeked layout. Returns the number of entries queued.
  const evaluate = () => {
    if (!active.size) return 0;
    const time = window.performance.now();
    const cache = newCache();
    let queued = 0;
    for (const observer of Array.from(active).sort(byId)) {
      const own = internals.get(observer);
      if (own.delay > 0 && own.lastRun !== null && time - own.lastRun < own.delay) continue;
      own.lastRun = time;
      for (const registration of own.targets) {
        const g = geometry(registration.target, own, cache);
        const ratio = ratioOf(g);
        const index = g.intersects ? thresholdIndex(own.thresholds, ratio) : 0;
        // Chromium reports isIntersecting as 'a threshold is reached' (measured: a ratio of 0.4 under
        // threshold 0.5 is not intersecting, with its intersectionRect and ratio still given).
        const isIntersecting = index > 0;
        const visible = own.trackVisibility && isIntersecting;
        if (index === registration.previousThresholdIndex && visible === registration.previousIsVisible) continue;
        registration.previousThresholdIndex = index;
        registration.previousIsVisible = visible;
        registration.queued.push(makeEntry(time, g, ratio, isIntersecting, visible, registration.target));
        queued++;
      }
    }
    if (queued && !taskQueued) {
      taskQueued = true;
      queueTask(notify, 0);
    }
    return queued;
  };

  const install = (name, value) => Object.defineProperty(window, name, { value, writable: true, configurable: true, enumerable: false });
  install('IntersectionObserver', IntersectionObserver);
  install('IntersectionObserverEntry', IntersectionObserverEntry);
  Object.defineProperty(window, '__ftIo', { value: Object.freeze({ evaluate }), configurable: true });
})();
"""

#: Note added by strategy A, which installs ``IO_SYNC_JS``.
IO_SYNC_NOTE = (
    "IntersectionObserver entries computed once per captured frame on that frame's layout and delivered in a "
    "task on the page clock (ADR-052)"
)

# Strategy A pauses every animation, SVG timeline and <video> and seeks it per frame, so the browser's own
# record of what the page asked for is lost: a page's pause() on an animation we already paused changes
# nothing we can see. These wrappers write the page's requests onto the object instead:
#   __ftPageWantsPaused  true after the page's pause() / pauseAnimations(), false after play(),
#                        reverse() or unpauseAnimations();
#   __ftPageSeek         the current time the page assigned: ms through an animation's currentTime or
#                        startTime, seconds through a media element's currentTime or fastSeek();
#   __ftPlayFrom         a media element's currentTime (s) when the page called play(), read before
#                        playback starts, so that where the page started it does not depend on real time.
# FlashTrace's own calls are made through ftOwnCall (PAGE_HELPERS_JS), which sets __ftOwnCall on the
# object for the duration of the call, so they are not recorded.
ANIMATION_RECORDER_JS = r"""
(() => {
  // Replaces proto[name] by a function that, for the page's calls only, runs note(object, ...args) first.
  const wrapMethod = (proto, name, note) => {
    const original = proto && proto[name];
    if (typeof original !== 'function') return;
    Object.defineProperty(proto, name, { configurable: true, writable: true, value: function (...args) {
      if (!this.__ftOwnCall) note(this, ...args);
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
  if (typeof HTMLMediaElement !== 'undefined') {
    const proto = HTMLMediaElement.prototype;
    wrapMethod(proto, 'pause', (media) => { media.__ftPageWantsPaused = true; });
    wrapMethod(proto, 'play', (media) => { media.__ftPageWantsPaused = false; media.__ftPlayFrom = media.currentTime; });
    wrapMethod(proto, 'fastSeek', (media, seconds) => { media.__ftPageSeek = seconds; });
    wrapSetter(proto, 'currentTime', (media, seconds) => { media.__ftPageSeek = seconds; });
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
