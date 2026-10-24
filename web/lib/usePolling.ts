"use client";

import { useCallback, useEffect, useRef, useState } from "react";

/**
 * The fastest the UI ever asks the server for anything (docs/API.md: "polls
 * at most once per second"). It is a floor, not a target: the next request
 * starts 1000 ms after the previous one STARTED and only after it finished,
 * so requests never overlap and never come faster than 1 per second.
 */
export const POLL_INTERVAL_MS = 1000;

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
      try {
        const value = await fetcherRef.current();
        if (stopped) return;
        setState({ token, data: value, error: null });
        if (isDoneRef.current(value)) stopped = true;
      } catch (error) {
        if (stopped) return;
        setState((prev) => ({ token, data: prev.token === token ? prev.data : null, error }));
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
 * and resolves with the first value for which isDone is true, or null when
 * isCancelled() says the caller has gone away (the screen unmounted).
 */
export async function pollUntil<T>(
  fetcher: () => Promise<T>,
  isDone: (value: T) => boolean,
  isCancelled: () => boolean,
): Promise<T | null> {
  for (;;) {
    await new Promise((resolve) => setTimeout(resolve, POLL_INTERVAL_MS));
    if (isCancelled()) return null;
    const value = await fetcher();
    if (isDone(value)) return value;
  }
}
