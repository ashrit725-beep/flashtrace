"use client";

import { LayoutGroup, motion } from "motion/react";
import { usePathname } from "next/navigation";
import { useLayoutEffect, useState, type ReactNode } from "react";
import { flushSync } from "react-dom";
import { DUR, EASE, GATE_MS, PAGE_CLOCK, setDisplayedRoute, useReducedMotionPreference } from "@/lib/motion";

// Route transitions (web/MOTION.md 3.0): Motion, enter-only, opacity only.
// A template remounts once per route segment and runs in the browser, so this
// works in the static export and on the clocks FlashTrace's capture controls.
//
// SAFETY: two gates, both kept under reduced motion (they are safety, not motion).
// 1. Animated entrances are at least GATE_MS.routeAnim (1 s) apart; a route
//    that mounts sooner simply appears.
// 2. Route display gate: Back, Forward, a held Alt+Left, a back-forward cache
//    restore or two links clicked in quick succession change routes without
//    any control's gate, up to ~30 times a second. Every mount changes what is
//    on screen (the old page goes), and so does showing a held page. A route
//    that mounts less than GATE_MS.routeDisplay (500 ms) after the last of
//    those display changes is held at opacity 0 (paper, layout intact,
//    aria-busy) and shown, without an entrance, 500 ms after its own mount; a
//    later mount replaces it and starts its own wait. A burst therefore shows:
//    old page, blank, final page.
//    The gate is measured from the last display change, not the last mount:
//    measured from mounts, a mount just after a held page appeared was shown
//    at once, and Back then Forward 50 ms later, every 450 ms, flashed 8 times
//    a second at 390 px (blank, page, page, blank in about 100 ms). 500 ms, not
//    350: a page shown at once followed by a held mount is two changes the
//    gate cannot prevent, so the next such pair must start a second later.

/** Module scope: survives template remounts, written only in effects and timers. */
const routeClock = {
  firstLoadDone: false,
  /** The last time what the route area shows changed: a mount, or a held page shown. */
  lastChangeAt: Number.NEGATIVE_INFINITY,
  lastAnimatedAt: Number.NEGATIVE_INFINITY,
};

type Mode = "first" | "enter" | "plain" | "held";

/** Decided once per mount (useState initializer). Reads module state, never writes it. */
function decideMode(): { mode: Mode; at: number } {
  if (typeof window === "undefined" || !routeClock.firstLoadDone) return { mode: "first", at: 0 };
  const at = performance.now();
  if (at - routeClock.lastChangeAt < GATE_MS.routeDisplay) return { mode: "held", at };
  if (at - routeClock.lastAnimatedAt < GATE_MS.routeAnim) return { mode: "plain", at };
  return { mode: "enter", at };
}

export default function Template({ children }: { children: ReactNode }) {
  const pathname = usePathname() || "/";
  const reduce = useReducedMotionPreference();
  const [{ mode, at }] = useState(decideMode);
  const [held, setHeld] = useState(mode === "held");

  // A layout effect: it runs inside the commit that mounted this route, so the
  // hold timer and the displayed route are set on the same frame as the page,
  // not in a later task.
  useLayoutEffect(() => {
    // First load: flip the flag here, never during render (Strict Mode renders twice).
    const now = mode === "first" ? performance.now() : at;
    routeClock.firstLoadDone = true;
    routeClock.lastChangeAt = now; // this mount replaced the old page (with this one, or with paper)
    if (mode === "enter" && !reduce) routeClock.lastAnimatedAt = now;
    let timer: ReturnType<typeof setTimeout> | undefined;
    // flushSync: the held page (and the nav that marks it) appear in the frame the timer fires.
    const show = () => {
      routeClock.lastChangeAt = performance.now();
      flushSync(() => {
        setHeld(false);
        setDisplayedRoute(pathname);
      });
    };
    if (mode === "held") timer = setTimeout(show, GATE_MS.routeDisplay);
    else setDisplayedRoute(pathname);

    // A full document restored from the back-forward cache goes through the same gate.
    const onPageShow = (event: PageTransitionEvent) => {
      if (!event.persisted) return;
      const t = performance.now();
      const tooSoon = t - routeClock.lastChangeAt < GATE_MS.routeDisplay;
      routeClock.lastChangeAt = t;
      if (!tooSoon) return;
      clearTimeout(timer);
      setHeld(true);
      timer = setTimeout(show, GATE_MS.routeDisplay);
    };
    window.addEventListener("pageshow", onPageShow);
    return () => {
      clearTimeout(timer);
      window.removeEventListener("pageshow", onPageShow);
    };
    // Runs once per mount: mode and at never change for this instance.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const entering = mode === "enter" && !reduce;
  return (
    // One LayoutGroup per route: useId values repeat across pages, and a
    // matching layoutId would otherwise glide in from the previous page.
    <LayoutGroup id={pathname}>
      <motion.div
        {...PAGE_CLOCK}
        className="route"
        initial={entering ? { opacity: 0 } : mode === "held" ? { opacity: 0 } : false}
        animate={{ opacity: held ? 0 : 1 }}
        transition={entering && !held ? { duration: DUR.base, ease: EASE.out } : { duration: 0 }}
        aria-busy={held || undefined}
        // A held route reserves a full viewport, so the footer below does not jump between each
        // page's height during a Back/Forward burst; it returns once, with the final page.
        style={held ? { minHeight: "100vh" } : undefined}
      >
        {children}
      </motion.div>
    </LayoutGroup>
  );
}
