"use client";

// The three non-happy states every screen must have (build brief): loading,
// empty and error-with-Retry. Kept deliberately static: no spinner, because
// nothing in this UI may move (docs/SPEC.md section 9).

import { useEffect, useRef, type ReactNode } from "react";
import { ApiError, NETWORK_ERROR_STATUS } from "@/lib/api";

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

export function LoadingState({ label }: { label: string }) {
  // role="status" makes screen readers announce the text politely.
  return (
    <div className="state state-loading" role="status">
      <p>{label}…</p>
    </div>
  );
}

interface EmptyStateProps {
  title: string;
  children?: ReactNode;
  /** Set when this state appears because of something the user just did (see useFocusWhenLost). */
  focusOnMount?: boolean;
}

export function EmptyState({ title, children, focusOnMount = false }: EmptyStateProps) {
  const heading = useFocusWhenLost<HTMLHeadingElement>(focusOnMount);
  return (
    <div className="state">
      <h2 ref={heading} tabIndex={focusOnMount ? -1 : undefined}>
        {title}
      </h2>
      {children}
    </div>
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
  // role="alert" so the failure is announced as soon as it appears.
  return (
    <div className="state state-error" role="alert">
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
    </div>
  );
}
