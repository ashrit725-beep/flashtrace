"use client";

// Motion tokens and the hooks every animated piece of the UI goes through
// (web/MOTION.md section 2). No JSX here: components live in components/motion.
//
// SAFETY (docs/DECISIONS.md ADR-024, docs/SPEC.md section 9): every animation
// is one-shot and ends within 600 ms of its trigger, nothing loops, and under
// reduced motion nothing moves at all. The gates below are safety, not motion:
// they stay on under reduced motion.

import { MotionGlobalConfig, type Transition } from "motion/react";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";
import { flushSync } from "react-dom";
import { onPageClock } from "./usePolling";
import { MIN_STEP_MS, useSafeToggle, type SafeToggle } from "./useSafeToggle";

// ------------------------------------------------------------------ tokens

/**
 * Durations in seconds (Motion's unit). CSS mirrors these as --dur-* in globals.css.
 * Every component duration comes from this scale: no literal seconds in a
 * component. Paired motions (the Running counter and its bar) share one token so
 * they land on the same frame, and a small scale keeps the motion one system.
 */
export const DUR = { instant: 0, press: 0.12, quick: 0.16, base: 0.24, emphasis: 0.42, max: 0.6 } as const;

type Bezier = [number, number, number, number];

/** out: entrances and responses to input. inOut: something already on screen moving (never a large-area fade). draw: paths and bars. */
export const EASE: { out: Bezier; inOut: Bezier; draw: Bezier } = {
  out: [0.23, 1, 0.32, 1],
  inOut: [0.77, 0, 0.175, 1],
  draw: [0.65, 0, 0.35, 1],
};

/** The one spring: no bounce, settles well inside 600 ms (tests/motion.spec.ts runs it to rest). Interruptible moves only. */
export const SPRING = { glide: { type: "spring", visualDuration: 0.24, bounce: 0 } } as const;

export const STAGGER = { step: 0.04, max: 0.24 } as const;

/** Entrance offsets in px. */
export const SHIFT = { xs: 4, s: 8 } as const;

/**
 * Rate gates in ms. step and largeArea are the 350 ms of lib/useSafeToggle
 * (about 3 changes a second at most).
 *
 * viewerStep and routeDisplay are 500 ms (about 2 a second), because those
 * screens can add a change the gate cannot hold back: closing the frame viewer
 * is never gated, and a route mount always replaces the page (MOTION.md 8).
 * With 350 ms, open, step, step and close fitted four changes into 750 ms, and
 * a Back/Forward pair every 450 ms reached 8 flashes a second at 390 px.
 */
export const GATE_MS = {
  step: MIN_STEP_MS,
  largeArea: MIN_STEP_MS,
  viewerStep: 500,
  dialogOpen: 1000,
  routeAnim: 1000,
  routeDisplay: 500,
  remove: 1000,
} as const;

/**
 * Delay of item i of n in a staggered entrance, in seconds. The whole stagger
 * never exceeds `max` (240 ms by default), however many items there are, so a
 * choreography still ends inside the 600 ms budget.
 */
export const staggerDelay = (i: number, n: number, max: number = STAGGER.max) =>
  Math.min(STAGGER.step, max / Math.max(1, n - 1)) * i;

const noUpdate = () => undefined;

/**
 * Spread onto every motion element that animates opacity: `{...PAGE_CLOCK}`.
 * With an onUpdate prop Motion runs the fade on requestAnimationFrame and
 * performance.now(), like every other value here (y, scale, pathLength,
 * layout), instead of handing it to a Web Animation. Why: Motion starts a Web
 * Animation by setting animation.startTime from its own clock. Under
 * FlashTrace's capture the page clock is fake but the animation timeline is
 * real, so the fade could count as long finished and commit its end before
 * the capture paused it: two captures of the same screen differed (self-audit
 * E1). It looks the same in a browser, and opacity stays a composite-only
 * property; only its ticking moves to the clock the capture controls.
 */
export const PAGE_CLOCK = { onUpdate: noUpdate } as const;

/** A click from Enter or Space has detail 0: keyboard-started movement is instant (MOTION.md principle 3). */
export const isKeyboardClick = (e: { detail: number }) => e.detail === 0;

// --------------------------------------------------------- reduced motion

const REDUCE_QUERY = "(prefers-reduced-motion: reduce)";
/** localStorage key of the in-app "Reduce motion" switch in the footer. */
export const MOTION_STORAGE_KEY = "flashtrace-motion";

function systemReduce(): boolean {
  try {
    return window.matchMedia(REDUCE_QUERY).matches;
  } catch {
    return false;
  }
}

function inAppReduce(): boolean {
  return document.documentElement.dataset.motion === "reduce";
}

/** True when the OS asks for reduced motion or the in-app switch is on. Always false on the server. */
export function readReducedMotion(): boolean {
  if (typeof window === "undefined") return false;
  return systemReduce() || inAppReduce();
}

/**
 * One subscription for both sources: the media query and the data-motion
 * attribute on <html> (set by the footer switch, by the inline head script
 * from localStorage, or by the self-audit's pre-roll). Motion's global skip
 * flag is updated before React hears about the change.
 */
function subscribe(onChange: () => void) {
  if (typeof window === "undefined") return () => undefined;
  const handler = () => {
    MotionGlobalConfig.skipAnimations = readReducedMotion();
    onChange();
  };
  let query: MediaQueryList | null = null;
  try {
    query = window.matchMedia(REDUCE_QUERY);
    query.addEventListener("change", handler);
  } catch {
    query = null;
  }
  const observer = new MutationObserver(handler);
  observer.observe(document.documentElement, { attributes: true, attributeFilter: ["data-motion"] });
  return () => {
    query?.removeEventListener("change", handler);
    observer.disconnect();
  };
}

const serverFalse = () => false;

/**
 * Room for a glide of something dense over evidence (the culprit outline and
 * its label): the WCAG reference width or wider. The flash window scales with
 * the viewport (341 x 256 px at 1024, 128 x 96 px at 390), but a 3 px outline
 * and a 12 px label do not, so the same glide that is a few sparse strokes at
 * 1280 sweeps most of a phone's window on the same frames (self-audit F3 at
 * 390 px: 6 flashes a second). Narrower screens move such things instantly.
 */
const GLIDE_ROOM_QUERY = "(min-width: 1024px)";

function subscribeGlideRoom(onChange: () => void) {
  if (typeof window === "undefined") return () => undefined;
  try {
    const query = window.matchMedia(GLIDE_ROOM_QUERY);
    query.addEventListener("change", onChange);
    return () => query.removeEventListener("change", onChange);
  } catch {
    return () => undefined;
  }
}

function readGlideRoom(): boolean {
  try {
    return window.matchMedia(GLIDE_ROOM_QUERY).matches;
  } catch {
    return false;
  }
}

/** True when the viewport is wide enough for a dense glide (see GLIDE_ROOM_QUERY). false on the server. */
export function useGlideRoom(): boolean {
  return useSyncExternalStore(subscribeGlideRoom, readGlideRoom, serverFalse);
}

/** Effective reduced-motion preference (OS or in-app). false during server rendering and hydration. */
export function useReducedMotionPreference(): boolean {
  return useSyncExternalStore(subscribe, readReducedMotion, serverFalse);
}

export function useSystemReducedMotion(): boolean {
  return useSyncExternalStore(subscribe, () => typeof window !== "undefined" && systemReduce(), serverFalse);
}

export function useInAppReducedMotion(): boolean {
  return useSyncExternalStore(subscribe, () => typeof window !== "undefined" && inAppReduce(), serverFalse);
}

/** The footer switch. Storage can be unavailable (private window, blocked site data): the attribute still applies for this page. */
export function setInAppReducedMotion(on: boolean) {
  const root = document.documentElement;
  if (on) root.dataset.motion = "reduce";
  else delete root.dataset.motion;
  MotionGlobalConfig.skipAnimations = readReducedMotion();
  try {
    if (on) window.localStorage.setItem(MOTION_STORAGE_KEY, "reduce");
    else window.localStorage.removeItem(MOTION_STORAGE_KEY);
  } catch {
    // storage blocked: the setting lasts until the page is reloaded
  }
}

export interface MotionTokens {
  reduce: boolean;
  /** `initial` for an entrance: the hidden state, or false (first frame is already final) under reduced motion. */
  from: <T>(hidden: T) => T | false;
  /** A bounded tween; zero length under reduced motion. */
  tween: (duration: number, options?: { ease?: Bezier; delay?: number }) => Transition;
  /** The glide spring, or instant (reduced motion, or a keyboard-started move). */
  glide: (instant?: boolean) => Transition;
}

const INSTANT: Transition = { duration: 0 };

/** Reduced-aware transition helpers. Every motion component takes its `initial` and `transition` from here. */
export function useMotionTokens(): MotionTokens {
  const reduce = useReducedMotionPreference();
  return useMemo<MotionTokens>(
    () => ({
      reduce,
      from: <T,>(hidden: T) => (reduce ? false : hidden),
      tween: (duration, options = {}) =>
        reduce ? INSTANT : { type: "tween", duration, ease: options.ease ?? EASE.out, delay: options.delay ?? 0 },
      glide: (instant = false) => (reduce || instant ? INSTANT : { ...SPRING.glide }),
    }),
    [reduce],
  );
}

// ------------------------------------------------------------------- gates

type Timer = ReturnType<typeof setTimeout> | undefined;

/**
 * Leading plus trailing gate for actions: the first call runs at once; calls
 * inside the next `ms` collapse into one call when the window ends, and the
 * last one wins. Nothing the user asked for is lost, and the action never runs
 * more often than once per `ms`. `restart()` starts a new window now (a pending
 * call then waits for it): the frame viewer restarts its open gate when it has
 * finished closing, so the viewer is shut for at least `ms` between two opens.
 */
export interface TrailingGate {
  run: (action: () => void) => void;
  restart: () => void;
}

/** The gate itself, outside React: module scope when several components must share one (the masthead's links). */
export function createTrailingGate(ms: number): TrailingGate & { dispose: () => void } {
  let last = Number.NEGATIVE_INFINITY;
  let pending: (() => void) | null = null;
  let timer: Timer = undefined;
  const fire = () => {
    timer = undefined;
    last = performance.now();
    const next = pending;
    pending = null;
    next?.();
  };
  return {
    run(action) {
      const now = performance.now();
      if (timer === undefined && now - last >= ms) {
        last = now;
        action();
        return;
      }
      pending = action;
      if (timer !== undefined) return;
      timer = setTimeout(fire, Math.max(0, last + ms - now));
    },
    restart() {
      last = performance.now();
      if (timer === undefined) return;
      clearTimeout(timer);
      timer = setTimeout(fire, ms);
    },
    dispose() {
      clearTimeout(timer);
      timer = undefined;
      pending = null;
    },
  };
}

/** One gate per component instance; a pending action is dropped when the component unmounts. */
export function useTrailingGate(ms: number): TrailingGate {
  const [gate] = useState(() => createTrailingGate(ms));
  useEffect(() => () => gate.dispose(), [gate]);
  return useMemo(() => ({ run: gate.run, restart: gate.restart }), [gate]);
}

/**
 * The value, changing at most once per `ms`: the first change applies at once,
 * later ones inside the window collapse into one trailing change with the
 * final value. Used where churn on an input would otherwise toggle a large
 * area (the custom-size row).
 */
export function useThrottledValue<T>(value: T, ms: number = GATE_MS.step): T {
  const [shown, setShown] = useState(value);
  const shownRef = useRef(value);
  const latest = useRef(value);
  const lastApplied = useRef(Number.NEGATIVE_INFINITY);
  const timer = useRef<Timer>(undefined);

  // A layout effect, so the timer is set inside the commit that changed the
  // value. A passive effect can run in a later task (after a router
  // transition), which would let the change land a frame later in one run
  // than in another.
  useLayoutEffect(() => {
    latest.current = value;
    if (timer.current !== undefined || Object.is(value, shownRef.current)) return;
    const apply = () => {
      timer.current = undefined;
      if (Object.is(latest.current, shownRef.current)) return; // back where it was: nothing to show
      lastApplied.current = performance.now();
      shownRef.current = latest.current;
      // Committed at once, in the frame the timer fires (a timer is outside React's own batching).
      flushSync(() => setShown(latest.current));
    };
    timer.current = setTimeout(apply, Math.max(0, lastApplied.current + ms - performance.now()));
  }, [value, ms]);
  useEffect(() => () => clearTimeout(timer.current), []);
  return shown;
}

// ------------------------------------------------------- displayed route

/**
 * The route whose page is on screen, as opposed to the router's latest
 * location. app/template.tsx sets it when a page becomes visible (at once, or
 * when a page held by the route display gate is shown); the nav marks this
 * route. During a Back/Forward burst the nav therefore stays on the old page
 * and moves once, with the final page, instead of following every step.
 */
let displayedRoute: string | null = null;
const displayedRouteListeners = new Set<() => void>();

export function setDisplayedRoute(path: string) {
  if (displayedRoute === path) return;
  displayedRoute = path;
  displayedRouteListeners.forEach((listener) => listener());
}

const subscribeDisplayedRoute = (listener: () => void) => {
  displayedRouteListeners.add(listener);
  return () => {
    displayedRouteListeners.delete(listener);
  };
};

/** The displayed route; `fallback` (the router's path) until a page has been shown, and on the server. */
export function useDisplayedRoute(fallback: string): string {
  return useSyncExternalStore(
    subscribeDisplayedRoute,
    () => displayedRoute ?? fallback,
    () => fallback,
  );
}

/**
 * One drop-style gate shared by every control on a screen whose consequences
 * overlap (the report's heatmap overlay and data-table disclosure both change
 * the same cells): two independent 350 ms gates would allow about 6 changes a
 * second between them. Without a provider each caller gets its own gate.
 */
export const LargeAreaGateContext = createContext<SafeToggle | null>(null);

/**
 * A cool-down measured from when something actually happened (mark), not from
 * when it was asked for: History accepts a delete at most once per
 * GATE_MS.remove after the previous row really left; a delete that failed
 * changed nothing, so it does not start the wait.
 */
export function useCooldown(ms: number): { ready: () => boolean; mark: () => void } {
  const last = useRef(Number.NEGATIVE_INFINITY);
  const ready = useCallback(() => performance.now() - last.current >= ms, [ms]);
  const mark = useCallback(() => {
    last.current = performance.now();
  }, []);
  return useMemo(() => ({ ready, mark }), [ready, mark]);
}

export function useLargeAreaGate(): SafeToggle {
  const shared = useContext(LargeAreaGateContext);
  const local = useSafeToggle(GATE_MS.largeArea);
  return shared ?? local;
}

// ------------------------------------------------------- in-view arrival

/**
 * unknown: not observed yet, so shown in its final state (never hidden on a
 * guess). waiting: the observer reported it off screen, so it is hidden there.
 * play: it came on screen after waiting: animate in. instant: show the final
 * state now (already on screen when first observed, focus, print, reduced motion).
 */
export type Arrival = "unknown" | "waiting" | "play" | "instant";

/**
 * Trigger for a below-the-fold entrance (MOTION.md principle 7). Content is
 * hidden only after an IntersectionObserver has reported it off screen, so it
 * is never invisible because a script, an observer or motion did not run. It
 * then arrives once, as soon as any part of it is on screen (amount "some": a
 * group taller than the viewport still arrives). Focus moving inside, or the
 * page being printed, shows the final state at once, so keyboard focus never
 * lands on something invisible. Disabled (reduced motion): final state always.
 *
 * SAFETY: disabling settles the state as well. A group that was hidden off
 * screen ("waiting") and scrolled into view while reduced motion was on is on
 * screen in its final state; turning reduced motion off must not hide it again
 * and replay its entrance (the self-audit's S1 measured 4 flash events from one
 * press of the footer switch at 390 px before this). "waiting" and "play"
 * therefore become "instant" while disabled, and an observer created after
 * that never changes "instant".
 */
export function useInViewArrival<T extends Element>(enabled: boolean) {
  const ref = useRef<T>(null);
  const [arrival, setArrival] = useState<Arrival>("unknown");
  // Adjusted while rendering (React's pattern for state derived from a prop), so the
  // first render with motion enabled again already holds "instant".
  if (!enabled && (arrival === "waiting" || arrival === "play")) setArrival("instant");

  useEffect(() => {
    const el = ref.current;
    if (!enabled || !el) return;
    const observer = new IntersectionObserver((entries) => {
      const visible = entries.some((e) => e.isIntersecting);
      // First report on screen: it is already visible, so it stays as it is (no replay).
      // Applied in the next animation frame (onPageClock), so an entrance starts at a frame-aligned time.
      void onPageClock(() =>
        setArrival((now) => (now === "unknown" ? (visible ? "instant" : "waiting") : now === "waiting" && visible ? "play" : now)),
      );
      if (visible) observer.disconnect();
    });
    observer.observe(el);
    const reveal = () => setArrival((now) => (now === "unknown" || now === "waiting" ? "instant" : now));
    const onFocus = reveal;
    const onPrint = reveal;
    el.addEventListener("focusin", onFocus);
    window.addEventListener("beforeprint", onPrint);
    return () => {
      observer.disconnect();
      el.removeEventListener("focusin", onFocus);
      window.removeEventListener("beforeprint", onPrint);
    };
  }, [enabled]);

  return [ref, enabled ? arrival : "instant"] as const;
}
