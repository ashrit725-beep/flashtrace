"use client";

// The three non-happy states every screen must have (build brief): loading,
// empty and error-with-Retry. Nothing here loops: no spinner and no shimmer
// (docs/SPEC.md section 9). Empty and error states fade in once (160 ms) when
// they arrive with data; a state that takes focus appears at once instead, so
// keyboard focus never lands on something invisible (web/MOTION.md 3.0).

import { motion } from "motion/react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { ApiError, NETWORK_ERROR_STATUS } from "@/lib/api";
import { DUR, PAGE_CLOCK, SHIFT, useMotionTokens } from "@/lib/motion";

/**
 * Moves focus to the returned element when it mounts, if `enabled`, and only
 * when focus has been lost. That happens when the control the user just
 * pressed (Retry, Cancel, Delete…) was removed, so the browser dropped focus
 * on <body> and the next Tab would start again at the skip link. If focus is
 * anywhere else the user put it there, and we leave it alone.
 */
function useFocusWhenLost<T extends HTMLElement>(enabled: boolean) {
  const ref = useRef<T>(null);
  useEffect(() => {
    const active = document.activeElement;
    const lost = active === null || active === document.body;
    if (enabled && lost) ref.current?.focus();
  }, [enabled]);
  return ref;
}

/** How long a request may take before its loading copy appears: a fast local answer never flickers "Loading…" then content. */
export const LOADING_DELAY_MS = 250;

export function LoadingState({ label }: { label: string }) {
  const [shown, setShown] = useState(false);
  useEffect(() => {
    const timer = setTimeout(() => setShown(true), LOADING_DELAY_MS);
    return () => clearTimeout(timer);
  }, []);
  // role="status" makes screen readers announce the text politely when it is
  // added. The box keeps its height while empty, so nothing moves when it fills.
  return (
    <div className="state state-loading" role="status" data-shown={shown ? "true" : "false"}>
      <p>{shown ? `${label}…` : ""}</p>
    </div>
  );
}

/** Static drawings for empty states: 72 x 56, 1.5 px strokes, one accent stroke, no motion. */
export type Illustration = "ledger" | "frame" | "trace";

function EmptyDrawing({ kind }: { kind: Illustration }) {
  return (
    <svg className="empty-drawing" width="72" height="56" viewBox="0 0 72 56" aria-hidden="true" focusable="false">
      {kind === "ledger" && (
        <>
          <rect x="10" y="4" width="52" height="48" rx="3" fill="none" stroke="currentColor" strokeWidth="1.5" />
          <path d="M18 14h36M18 22h36M18 30h20" fill="none" stroke="currentColor" strokeWidth="1.5" />
          <path d="M18 42h36" fill="none" stroke="var(--accent)" strokeWidth="1.5" />
        </>
      )}
      {kind === "frame" && (
        <>
          <path
            d="M6 14V6h8M58 6h8v8M66 42v8h-8M14 50H6v-8"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.5"
          />
          <rect x="14" y="14" width="44" height="28" fill="none" stroke="currentColor" strokeWidth="1.5" strokeDasharray="3 3" />
          <path d="M22 34h28" fill="none" stroke="var(--accent)" strokeWidth="1.5" />
        </>
      )}
      {kind === "trace" && (
        <>
          <rect x="6" y="6" width="60" height="44" rx="3" fill="none" stroke="currentColor" strokeWidth="1.5" />
          <path d="M12 34h48" fill="none" stroke="var(--accent)" strokeWidth="1.5" />
          <path d="M12 42h20" fill="none" stroke="currentColor" strokeWidth="1.5" />
        </>
      )}
    </svg>
  );
}

/** A box that arrives with data: one fade (opacity, 4 px rise), or nothing at all when it takes focus or motion is reduced. */
function Arriving({ className, role, still, children }: { className: string; role?: "alert"; still: boolean; children: ReactNode }) {
  const m = useMotionTokens();
  return (
    <motion.div
      {...PAGE_CLOCK}
      className={className}
      role={role}
      initial={still ? false : m.from({ opacity: 0, y: SHIFT.xs })}
      animate={{ opacity: 1, y: 0 }}
      transition={m.tween(DUR.quick)}
    >
      {children}
    </motion.div>
  );
}

interface EmptyStateProps {
  title: string;
  children?: ReactNode;
  /** Set when this state appears because of something the user just did (see useFocusWhenLost). */
  focusOnMount?: boolean;
  /** An optional small drawing; the copy always carries the state. */
  illustration?: Illustration;
}

export function EmptyState({ title, children, focusOnMount = false, illustration }: EmptyStateProps) {
  const heading = useFocusWhenLost<HTMLHeadingElement>(focusOnMount);
  return (
    <Arriving className={`state${illustration ? " state-with-drawing" : ""}`} still={focusOnMount}>
      {illustration && <EmptyDrawing kind={illustration} />}
      <div className="state-body">
        <h2 ref={heading} tabIndex={focusOnMount ? -1 : undefined}>
          {title}
        </h2>
        {children}
      </div>
    </Arriving>
  );
}

interface ErrorStateProps {
  title: string;
  error?: unknown;
  onRetry?: () => void;
  retryLabel?: string;
  /** Set when this error can follow the user's own action (Retry, submit, cancel, delete). */
  focusOnMount?: boolean;
  children?: ReactNode;
}

/** Turns anything thrown into one readable sentence. */
export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    return error.status === NETWORK_ERROR_STATUS
      ? "Could not reach the FlashTrace server. Is it running?"
      : error.detail;
  }
  if (error instanceof Error && error.message) return error.message;
  return "Something went wrong.";
}

export function ErrorState({ title, error, onRetry, retryLabel = "Retry", focusOnMount = false, children }: ErrorStateProps) {
  const heading = useFocusWhenLost<HTMLHeadingElement>(focusOnMount);
  // role="alert" so the failure is announced as soon as it appears, whatever the animation does.
  return (
    <Arriving className="state state-error" role="alert" still={focusOnMount}>
      <h2 ref={heading} tabIndex={focusOnMount ? -1 : undefined}>
        {title}
      </h2>
      {error !== undefined && <p>{errorMessage(error)}</p>}
      {children}
      {onRetry && (
        <div className="btn-row">
          <button type="button" className="btn btn-primary" onClick={onRetry}>
            {retryLabel}
          </button>
        </div>
      )}
    </Arriving>
  );
}
