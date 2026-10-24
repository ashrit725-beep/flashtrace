"use client";

import { useCallback, useRef, type KeyboardEvent } from "react";

/**
 * SAFETY (docs/SPEC.md section 9): nothing this UI shows may change faster
 * than 3 times a second. A control that swaps a large area (the heatmap
 * overlay on a black-and-white still, a data table that pushes the evidence
 * frames up and down, the frame viewer's next/previous) turns into a strobe
 * if it can flip at keyboard auto-repeat speed (holding Enter repeats about
 * 30 times a second) or under fast clicking. Changes closer than 350 ms are
 * dropped, so a control can change at most about 3 times a second (an on/off
 * cycle at most about 1.5 times a second).
 */
export const MIN_STEP_MS = 350;

/**
 * Holding Enter on a <button> fires a click on every key repeat. Cancelling
 * the repeated keydown cancels that click, so a held key acts once. (Space
 * clicks on key-up, which never repeats.) Works on the button itself or on
 * any ancestor, because React handles the event before the browser's default.
 */
export function blockKeyRepeat(event: KeyboardEvent) {
  if (event.repeat && event.key === "Enter") event.preventDefault();
}

export interface SafeToggle {
  /**
   * Runs `change` unless the last change ran less than MIN_STEP_MS ago.
   * `change` may return false to say "nothing changed" (for example already
   * at the last frame); that does not start a new wait.
   */
  run: (change: () => boolean | void) => void;
  /** Put this on the control's onKeyDown so a held Enter acts only once. */
  onKeyDown: (event: KeyboardEvent) => void;
}

/** One throttle per component: every control that uses the same `run` shares it. */
export function useSafeToggle(minMs: number = MIN_STEP_MS): SafeToggle {
  // -Infinity, not 0: performance.now() counts from page load, so 0 would
  // swallow a first change made within minMs of opening the page.
  const last = useRef(Number.NEGATIVE_INFINITY);
  const run = useCallback(
    (change: () => boolean | void) => {
      const now = performance.now();
      if (now - last.current < minMs) return;
      if (change() === false) return;
      last.current = now;
    },
    [minMs],
  );
  return { run, onKeyDown: blockKeyRepeat };
}
