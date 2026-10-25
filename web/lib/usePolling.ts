"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { flushSync } from "react-dom";

/**
 * The fastest the UI ever asks the server for anything (docs/API.md: "polls
 * at most once per second"). It is a floor, not a target: the next request
 * starts 1000 ms after the previous one STARTED and only after it finished,
 * so requests never overlap and never come faster than 1 per second.
 */
export const POLL_INTERVAL_MS = 1000;

/** A hidden tab runs no animation frames: apply the update after this long instead. */
const HIDDEN_TAB_FALLBACK_MS = 100;

/**
 * A network result is applied no sooner than this long after its request
 * started (page clock). Under FlashTrace's capture the request finishes while
 * virtual time stands still, but reading its body is a further browser task:
 * on a loaded machine it could finish one capture step later, and the result
 * then appeared a frame apart in two captures of the same screen (self-audit
 * F4, F4c and F7 under four concurrent captures). Measured from the request's
 * start, the moment is the same in every run. In a browser it adds at most
 * 50 ms, and only to an answer that came back faster than that.
 */
export const NETWORK_SETTLE_MS = 50;

/**
 * Applies a state update that arrives from outside an input event (a network
 * answer, an IntersectionObserver report) in the next animation frame, and
 * commits it at once (flushSync). A promise or observer callback is outside
 * React's batching, so React would commit it in a task of its own at a moment
 * set by machine speed. FlashTrace's self-audit captures this UI on a
 * controlled clock, and there the frame in which new data appeared (and the
 * time its animations started) differed between two captures of the same
 * screen. In an animation frame it lands at the same, frame-aligned time every
 * time; in a browser this costs at most one frame.
 */
export function onPageClock(update: () => void, requestStartedAt?: number): Promise<void> {
  return new Promise((resolve) => {
    let done = false;
    let frame = 0;
    let fallback: ReturnType<typeof setTimeout> | undefined;
    const run = () => {
      if (done) return;
      done = true;
      cancelAnimationFrame(frame);
      clearTimeout(fallback);
      flushSync(update);
      resolve();
    };
    const start = () => {
      frame = requestAnimationFrame(run);
      fallback = setTimeout(run, HIDDEN_TAB_FALLBACK_MS);
    };
    // A network result waits until NETWORK_SETTLE_MS after its request started (see above).
    const wait = requestStartedAt === undefined ? 0 : requestStartedAt + NETWORK_SETTLE_MS - performance.now();
    if (wait > 0) setTimeout(start, wait);
    else start();
  });
}

export interface PollState<T> {
  /** Latest successful answer (kept while a retry is in flight). */
  data: T | null;
  /** The error that stopped polling, or null. */
  error: unknown;
  /** True until the first answer or error arrives. */
  loading: boolean;
  /** Clear the error and start polling again. */
  retry: () => void;
  /**
   * True once retry() has been called. A screen uses it to tell an error that
   * follows the user's own Retry (the button they pressed is gone, so focus
   * must be moved to the new error) from one on a fresh page load.
   */
  retried: boolean;
}

/**
 * Calls `fetcher` now and then every POLL_INTERVAL_MS until `isDone(value)`
 * is true, a call throws, the key changes or the component unmounts. Pauses
 * while the tab is hidden (no point asking for progress nobody can see) and
 * resumes when it becomes visible again.
 *
 * `key` identifies what is being polled (usually a job ID); null disables
 * polling. The latest fetcher/isDone are read through refs so callers do not
 * need to memoise them.
 */
export function usePolling<T>(
  key: string | null,
  fetcher: () => Promise<T>,
  isDone: (value: T) => boolean,
): PollState<T> {
  // State remembers which key (and which retry run) it belongs to, so a new
  // key never shows the previous key's data or error.
  const [run, setRun] = useState(0);
  const [state, setState] = useState<{ token: string; data: T | null; error: unknown }>({
    token: "",
    data: null,
    error: null,
  });

  const fetcherRef = useRef(fetcher);
  const isDoneRef = useRef(isDone);
  useEffect(() => {
    fetcherRef.current = fetcher;
    isDoneRef.current = isDone;
  });

  useEffect(() => {
    if (key === null) return;
    const token = key;
    let stopped = false;
    let inFlight = false;
    let lastStart = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const schedule = () => {
      if (stopped || inFlight || timer !== undefined) return;
      const wait = Math.max(0, lastStart + POLL_INTERVAL_MS - Date.now());
      timer = setTimeout(tick, wait);
    };

    async function tick() {
      timer = undefined;
      if (stopped || inFlight) return;
      if (document.hidden) return; // the visibilitychange handler resumes us
      inFlight = true;
      lastStart = Date.now();
      const startedAt = performance.now();
      try {
        const value = await fetcherRef.current();
        if (stopped) return;
        await onPageClock(() => {
          if (!stopped) setState({ token, data: value, error: null });
        }, startedAt);
        if (isDoneRef.current(value)) stopped = true;
      } catch (error) {
        if (stopped) return;
        await onPageClock(() => {
          if (!stopped) setState((prev) => ({ token, data: prev.token === token ? prev.data : null, error }));
        }, startedAt);
        stopped = true;
      } finally {
        inFlight = false;
      }
      schedule();
    }

    const onVisibility = () => {
      if (document.hidden) {
        clearTimeout(timer);
        timer = undefined;
      } else {
        schedule();
      }
    };

    document.addEventListener("visibilitychange", onVisibility);
    void tick();
    return () => {
      stopped = true;
      clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [key, run]);

  const retry = useCallback(() => {
    setState((prev) => ({ ...prev, error: null }));
    setRun((n) => n + 1);
  }, []);

  const mine = key !== null && state.token === key;
  const data = mine ? state.data : null;
  const error = mine ? state.error : null;
  return { data, error, loading: key !== null && data === null && error === null, retry, retried: run > 0 };
}

/**
 * A one-shot load is a poll that is done after its first answer: same
 * loading/error/retry handling, exactly one request per key (or per retry).
 */
export function useLoad<T>(key: string | null, fetcher: () => Promise<T>): PollState<T> {
  return usePolling(key, fetcher, () => true);
}

/**
 * The same once-per-second rule for event handlers (where hooks cannot be
 * used), e.g. "Verify this fix": waits POLL_INTERVAL_MS before every request
 * and resolves with the first value for which isDone is true (and the page
 * clock time its request started, for onPageClock), or null when
 * isCancelled() says the caller has gone away (the screen unmounted).
 */
export async function pollUntil<T>(
  fetcher: () => Promise<T>,
  isDone: (value: T) => boolean,
  isCancelled: () => boolean,
): Promise<{ value: T; startedAt: number } | null> {
  for (;;) {
    await new Promise((resolve) => setTimeout(resolve, POLL_INTERVAL_MS));
    if (isCancelled()) return null;
    const startedAt = performance.now();
    const value = await fetcher();
    if (isDone(value)) return { value, startedAt };
  }
}
