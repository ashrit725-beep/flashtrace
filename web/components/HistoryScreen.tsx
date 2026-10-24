"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { deleteJob, isFinished, isReportReady, listJobs, type JobSummary } from "@/lib/api";
import { PHASE_LABEL, formatDateTime } from "@/lib/format";
import { useLoad } from "@/lib/usePolling";
import { EmptyState, ErrorState, LoadingState } from "./StatusViews";
import VerdictTag from "./VerdictTag";
import styles from "./History.module.css";

const STATUS_LABEL: Record<JobSummary["status"], string> = {
  queued: "Queued",
  running: "Running",
  done: "Done",
  error: "Error",
  cancelled: "Cancelled",
};

/** Where a job's main link should go: a readable report, otherwise its progress. */
function openHref(job: JobSummary) {
  const id = encodeURIComponent(job.id);
  return isReportReady(job) ? `/report/?id=${id}` : `/run/?id=${id}`;
}

/** Status plus phase in one phrase for the cards, without "Done, Done". */
function statusText(job: JobSummary): string {
  const status = STATUS_LABEL[job.status];
  const quiet = job.phase === "done" || job.phase === "error" || job.phase === "queued";
  return quiet ? status : `${status}, ${PHASE_LABEL[job.phase].toLowerCase()}`;
}

interface ActionsProps {
  job: JobSummary;
  busy: boolean;
  onDelete: (job: JobSummary) => void;
}

// Shared by the table and the cards. data-delete lets the screen find the
// visible Delete button for a job when it needs to move focus there.
function JobActions({ job, busy, onDelete }: ActionsProps) {
  const when = formatDateTime(job.createdAt);
  const open = isReportReady(job) ? "Open report" : "Open progress";
  return (
    <div className={styles.actions}>
      <Link className="btn btn-small" href={openHref(job)} aria-label={`${open} for ${job.url}, ${when}`}>
        {open}
      </Link>
      {/* aria-disabled, not disabled: a disabled button drops keyboard focus to <body>. */}
      <button
        type="button"
        className="btn btn-small btn-danger"
        data-delete={job.id}
        onClick={() => onDelete(job)}
        aria-disabled={busy}
        aria-label={`Delete audit of ${job.url}, ${when}`}
      >
        Delete
      </button>
    </div>
  );
}

/** Focus the Delete button for `id` in whichever layout (table or cards) is on screen. */
function focusDeleteButton(id: string | null): boolean {
  const all = Array.from(document.querySelectorAll<HTMLElement>("[data-delete]"));
  const candidates = id === null ? all : all.filter((b) => b.dataset.delete === id);
  // getClientRects() is empty for an element hidden by display: none.
  const visible = candidates.find((b) => b.getClientRects().length > 0);
  visible?.focus();
  return visible !== undefined;
}

// Screen 4 (/history). One request on load (no polling: a list of past
// audits does not need live updates); Retry or a delete reloads it.
export default function HistoryScreen() {
  const jobs = useLoad("jobs", listJobs);
  const [deleting, setDeleting] = useState<string | null>(null);
  const [deleteError, setDeleteError] = useState<{ job: JobSummary; error: unknown } | null>(null);
  const [deletedAny, setDeletedAny] = useState(false);
  const [message, setMessage] = useState("");
  // After a delete, the row (and its focused button) disappears when the list
  // reloads. This remembers where focus should go then: the next row's
  // Delete, or the previous row's if the last row was deleted.
  const focusAfterReload = useRef<{ deleted: string; next: string | null } | null>(null);

  useEffect(() => {
    const pending = focusAfterReload.current;
    if (!pending || !jobs.data || jobs.data.some((j) => j.id === pending.deleted)) return; // not reloaded yet
    focusAfterReload.current = null;
    if (jobs.data.length === 0) return; // the "No audits yet" heading takes focus itself
    if (!focusDeleteButton(pending.next)) focusDeleteButton(null); // that row went too: use the first one
  }, [jobs.data]);

  // Newest first, whatever order the server used.
  const sorted = [...(jobs.data ?? [])].sort((a, b) => b.createdAt.localeCompare(a.createdAt));

  // confirmFirst is false for the error box's Retry: the user already confirmed.
  async function remove(job: JobSummary, confirmFirst = true) {
    if (deleting !== null) return; // the buttons are only aria-disabled
    if (confirmFirst && !window.confirm(`Delete the audit of ${job.url}? Its report and frames will be removed.`)) return;
    const at = sorted.findIndex((j) => j.id === job.id);
    const next = sorted[at + 1] ?? sorted[at - 1] ?? null;
    setDeleting(job.id);
    setDeleteError(null);
    try {
      await deleteJob(job.id);
      focusAfterReload.current = { deleted: job.id, next: next?.id ?? null };
      setDeletedAny(true);
      setMessage(`Deleted the audit of ${job.url}.`);
      jobs.retry();
    } catch (error) {
      setDeleteError({ job, error });
    } finally {
      setDeleting(null);
    }
  }

  const busy = deleting !== null;

  return (
    <>
      <div className="page-head">
        <p className="kicker">History</p>
        <h1>Past audits</h1>
        <p className="lede">Every audit this server has run, newest first.</p>
      </div>

      <p className="visually-hidden" role="status">
        {message}
      </p>

      {jobs.loading && <LoadingState label="Loading past audits" />}
      {jobs.error !== null && (
        <ErrorState
          title="Could not load past audits"
          error={jobs.error}
          onRetry={jobs.retry}
          focusOnMount={jobs.retried}
        />
      )}
      {deleteError !== null && (
        <ErrorState
          title="Could not delete the audit"
          error={deleteError.error}
          onRetry={() => remove(deleteError.job, false)}
          focusOnMount
        >
          <p className="url">{deleteError.job.url}</p>
        </ErrorState>
      )}

      {jobs.data && sorted.length === 0 && (
        <EmptyState title="No audits yet" focusOnMount={deletedAny}>
          <p>
            Audits you run appear here. <Link href="/">Start a new audit</Link>.
          </p>
        </EmptyState>
      )}

      {sorted.length > 0 && (
        <>
          <div className={`table-wrap ${styles.tableView}`}>
            <table>
              <caption className="visually-hidden">Past audits, newest first</caption>
              <thead>
                <tr>
                  <th scope="col">Date</th>
                  <th scope="col">Page</th>
                  <th scope="col">Verdict</th>
                  <th scope="col">Status</th>
                  <th scope="col">Phase</th>
                  <th scope="col">Actions</th>
                </tr>
              </thead>
              <tbody>
                {sorted.map((job) => (
                  <tr key={job.id}>
                    <td className="num">{formatDateTime(job.createdAt)}</td>
                    <td className="url">{job.url}</td>
                    <td>
                      <VerdictTag verdict={job.verdict} pending={!isFinished(job)} />
                    </td>
                    <td>{STATUS_LABEL[job.status]}</td>
                    <td>{PHASE_LABEL[job.phase]}</td>
                    <td>
                      <JobActions job={job} busy={busy} onDelete={remove} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <ul className={styles.cards} aria-label="Past audits, newest first">
            {sorted.map((job) => (
              <li key={job.id} className={styles.card}>
                <p className="url">{job.url}</p>
                <dl className={styles.facts}>
                  <div>
                    <dt>Date</dt>
                    <dd>{formatDateTime(job.createdAt)}</dd>
                  </div>
                  <div>
                    <dt>Verdict</dt>
                    <dd>
                      <VerdictTag verdict={job.verdict} pending={!isFinished(job)} />
                    </dd>
                  </div>
                  <div>
                    <dt>Status</dt>
                    <dd>{statusText(job)}</dd>
                  </div>
                </dl>
                <JobActions job={job} busy={busy} onDelete={remove} />
              </li>
            ))}
          </ul>
        </>
      )}
    </>
  );
}
