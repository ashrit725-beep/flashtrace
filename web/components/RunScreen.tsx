"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import {
  ApiError,
  createJob,
  deleteJob,
  getJob,
  isFinished,
  isReportReady,
  type JobPhase,
  type JobSummary,
} from "@/lib/api";
import { ATTRIBUTE_LABEL, PHASE_LABEL, phaseText, sizeLabel } from "@/lib/format";
import { usePolling } from "@/lib/usePolling";
import { EmptyState, ErrorState, LoadingState, errorMessage } from "./StatusViews";
import styles from "./Run.module.css";

// The phases shown in the stepper, in order, for this job. A new audit lists
// every step its settings will run, ablation included, so the user sees the
// slow re-captures coming. A re-run on a finished audit (verifying a fix, or
// ablation requested later from the report) only has its own step to show.
function stepsFor(job: JobSummary): JobPhase[] {
  const { attribute } = job.settings;
  const rerun = job.phase === "verifying" || (job.phase === "ablating" && attribute !== "ablate");
  if (rerun) return ["queued", job.phase, "done"];
  const steps: JobPhase[] = ["queued", "capturing", "analyzing"];
  if (attribute !== "none") steps.push("attributing");
  if (attribute === "ablate") steps.push("ablating");
  steps.push("done");
  return steps;
}

const STEP_GLYPH = { complete: "✓", current: "›", pending: "" } as const;
const STEP_STATUS = { complete: "completed", current: "in progress", pending: "not started" } as const;

function Stepper({ job }: { job: JobSummary }) {
  const steps = stepsFor(job);
  const at = Math.max(0, steps.indexOf(job.phase));
  return (
    <ol className={styles.stepper} aria-label="Audit phases">
      {steps.map((step, i) => {
        const state = i < at || isReportReady(job) ? "complete" : i === at ? "current" : "pending";
        return (
          <li
            key={step}
            className={`${styles.step} ${styles[state] ?? ""}`}
            aria-current={state === "current" ? "step" : undefined}
          >
            <span className={styles.glyph} aria-hidden="true">
              {STEP_GLYPH[state]}
            </span>
            {step === "done" ? "Report ready" : PHASE_LABEL[step]}
            <span className="visually-hidden">, {STEP_STATUS[state]}</span>
          </li>
        );
      })}
    </ol>
  );
}

function Heading({ url }: { url?: string }) {
  return (
    <div className="page-head">
      <p className="kicker">Running audit</p>
      <h1>{url ? "Auditing the page" : "Audit progress"}</h1>
      {url && <p className="url">{url}</p>}
    </div>
  );
}

export default function RunScreen() {
  const id = useSearchParams().get("id");
  const router = useRouter();
  const [cancelled, setCancelled] = useState(false);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<unknown>(null);

  // Stop polling once we cancelled: the job no longer exists on the server.
  // isFinished (not status alone): after "Re-run attribution with ablation"
  // the job may keep status "done" while its phase is "ablating".
  const poll = usePolling(cancelled ? null : id, () => getJob(id ?? ""), isFinished);
  const job = poll.data;
  const ready = job !== null && isReportReady(job);

  // Done: go straight to the report. replace() keeps Back from bouncing here.
  useEffect(() => {
    if (job && isReportReady(job)) router.replace(`/report/?id=${encodeURIComponent(job.id)}`);
  }, [job, router]);

  // confirmFirst is false for the error box's Retry: the user already confirmed.
  async function cancel(confirmFirst = true) {
    if (!id || busy) return; // busy: the button is only aria-disabled
    // DELETE removes the whole audit, including a finished report when this is a re-run.
    if (confirmFirst && !window.confirm("Cancel this audit? The audit and all its files, including any report, will be deleted.")) return;
    setBusy(true);
    setActionError(null);
    try {
      await deleteJob(id);
      setCancelled(true);
    } catch (error) {
      setActionError(error);
    } finally {
      setBusy(false);
    }
  }

  // Retry = submit the same URL and settings again as a new job.
  async function retryJob(failed: JobSummary) {
    if (busy) return;
    setBusy(true);
    setActionError(null);
    try {
      const { id: newId } = await createJob({ url: failed.url, ...failed.settings });
      router.push(`/run/?id=${encodeURIComponent(newId)}`);
    } catch (error) {
      setActionError(error);
    } finally {
      setBusy(false);
    }
  }

  if (!id) {
    return (
      <>
        <Heading />
        <EmptyState title="No audit selected">
          <p>
            This page shows a running audit. <Link href="/">Start a new audit</Link> or open one from{" "}
            <Link href="/history/">History</Link>.
          </p>
        </EmptyState>
      </>
    );
  }

  if (cancelled) {
    return (
      <>
        <Heading />
        <EmptyState title="Audit cancelled" focusOnMount>
          <p>
            The audit was stopped and its files were deleted. <Link href="/">Start a new audit</Link>.
          </p>
        </EmptyState>
      </>
    );
  }

  if (poll.loading) {
    return (
      <>
        <Heading />
        <LoadingState label="Loading the audit's progress" />
      </>
    );
  }

  if (!job) {
    const notFound = poll.error instanceof ApiError && poll.error.status === 404;
    return (
      <>
        <Heading />
        {/* Retry even for a 404: the job may still be being created, or the server restarting. */}
        <ErrorState
          title={notFound ? "No audit with this ID" : "Could not load the audit's progress"}
          error={poll.error}
          onRetry={poll.retry}
          focusOnMount={poll.retried}
        >
          {notFound && (
            <p>
              It may have been deleted. <Link href="/history/">Open History</Link> or{" "}
              <Link href="/">start a new audit</Link>.
            </p>
          )}
        </ErrorState>
      </>
    );
  }

  if (job.status === "error") {
    return (
      <>
        <Heading url={job.url} />
        <div className="state state-error" role="alert">
          <h2>The audit stopped with an error</h2>
          <p>{job.error ?? "The server did not say why."}</p>
          {/* A failed Retry is reported inside this same box, whose Retry
              button repeats it; focus stays on that button (aria-disabled). */}
          {actionError !== null && (
            <p>
              <strong>Retry failed:</strong> {errorMessage(actionError)}
            </p>
          )}
          <div className="btn-row">
            <button type="button" className="btn btn-primary" onClick={() => retryJob(job)} aria-disabled={busy}>
              {busy ? "Retrying…" : "Retry"}
            </button>
            <Link className="btn" href="/">
              Back
            </Link>
          </div>
        </div>
      </>
    );
  }

  if (job.status === "cancelled") {
    return (
      <>
        <Heading url={job.url} />
        <EmptyState title="This audit was cancelled">
          <p>
            <Link href="/">Start a new audit</Link> or open another from <Link href="/history/">History</Link>.
          </p>
        </EmptyState>
      </>
    );
  }

  const { current, total } = job.progress;
  const text = phaseText(job);
  return (
    <>
      <Heading url={job.url} />
      <section aria-labelledby="phase-now">
        <h2 id="phase-now" className={styles.phase}>
          {ready ? "Done. Opening the report…" : text}
        </h2>
        {/* Announce phase changes only (not every frame count) to keep screen readers calm. */}
        <p className="visually-hidden" aria-live="polite">
          {PHASE_LABEL[job.phase]}
        </p>
        {total > 0 ? (
          <>
            <progress
              className={styles.bar}
              value={Math.min(current, total)}
              max={total}
              aria-labelledby="phase-now"
              aria-valuetext={text}
            />
            <p className={`${styles.barNote} num`}>{Math.round((Math.min(current, total) / total) * 100)}% of this step</p>
          </>
        ) : (
          <p className={styles.barNote}>This step has no frame count; it usually takes a few seconds.</p>
        )}

        <Stepper job={job} />

        <dl className={styles.settings}>
          <dt>Duration</dt>
          <dd>{job.settings.duration} s</dd>
          <dt>Viewport</dt>
          <dd>{sizeLabel(job.settings.viewport)} px</dd>
          <dt>Attribution</dt>
          <dd>{ATTRIBUTE_LABEL[job.settings.attribute] ?? job.settings.attribute}</dd>
        </dl>

        {poll.error !== null && (
          <ErrorState
            title="Lost contact with the server"
            error={poll.error}
            onRetry={poll.retry}
            focusOnMount={poll.retried}
          />
        )}
        {actionError !== null && (
          <ErrorState
            title="Could not cancel the audit"
            error={actionError}
            onRetry={() => cancel(false)}
            focusOnMount
          />
        )}

        <div className="btn-row">
          {ready ? (
            <Link className="btn btn-primary" href={`/report/?id=${encodeURIComponent(job.id)}`}>
              Open the report
            </Link>
          ) : (
            // aria-disabled, not disabled: a disabled button would drop keyboard focus to <body>.
            <button type="button" className="btn btn-danger" onClick={() => cancel()} aria-disabled={busy}>
              Cancel audit
            </button>
          )}
        </div>
      </section>
    </>
  );
}
