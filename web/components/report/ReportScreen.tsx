"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  ApiError,
  ablate,
  getJob,
  getReport,
  isFinished,
  reportHtmlUrl,
  reportJsonUrl,
  verifyFix,
  type Report,
} from "@/lib/api";
import { pollUntil, useLoad } from "@/lib/usePolling";
import { EmptyState, ErrorState, LoadingState, errorMessage } from "../StatusViews";
import CulpritsTable from "./CulpritsTable";
import EvidenceStrip from "./EvidenceStrip";
import { AdvisoryList, TextList, ViolationsList } from "./FindingsLists";
import FixPanel, { type VerifyFinished } from "./FixPanel";
import FrameDialog from "./FrameDialog";
import HeatmapView from "./HeatmapView";
import styles from "./Report.module.css";
import SummaryList from "./SummaryList";
import TimelineChart from "./TimelineChart";
import VerdictBanner from "./VerdictBanner";

function Heading() {
  return (
    <div className="page-head">
      <p className="kicker">Report</p>
      <h1>Audit report</h1>
    </div>
  );
}

interface ProblemProps {
  id: string;
  error: unknown;
  onRetry: () => void;
  /** True after the user pressed Retry, so a repeated failure takes focus (see ErrorState). */
  retried: boolean;
}

/** Loading, empty and error states for the report, decided by HTTP status. */
function ReportProblem({ id, error, onRetry, retried }: ProblemProps) {
  const status = error instanceof ApiError ? error.status : -1;
  const run = `/run/?id=${encodeURIComponent(id)}`;
  if (status === 409) {
    return (
      <EmptyState title="This audit has not finished yet">
        <p>
          The report is written when the audit is done. <Link href={run}>Watch its progress</Link>.
        </p>
      </EmptyState>
    );
  }
  if (status === 404) {
    // Still offers Retry: a 404 can be temporary (the server was restarting,
    // or the job is still being set up), and every error state has one.
    return (
      <ErrorState title="No report for this audit" error={error} onRetry={onRetry} focusOnMount={retried}>
        <p>
          The audit may have stopped before a report existed, or it was deleted. <Link href={run}>See the audit</Link>{" "}
          or open <Link href="/history/">History</Link>.
        </p>
      </ErrorState>
    );
  }
  return <ErrorState title="Could not load the report" error={error} onRetry={onRetry} focusOnMount={retried} />;
}

export default function ReportScreen() {
  const id = useSearchParams().get("id");
  const router = useRouter();
  const report = useLoad(id, () => getReport(id ?? ""));

  const [selectedRank, setSelectedRank] = useState<number | null>(null);
  const [dialogFrame, setDialogFrame] = useState<number | null>(null);
  const [verifying, setVerifying] = useState<number | null>(null);
  const [verifyError, setVerifyError] = useState<{ index: number; message: string } | null>(null);
  // The fix whose verification finished last, and the report object it
  // started from: the reloaded report is a new object, so "r !== from" tells
  // us the new After column has arrived and can be announced.
  const [verifyDone, setVerifyDone] = useState<{ index: number; from: Report } | null>(null);
  const [ablating, setAblating] = useState(false);
  const [ablateError, setAblateError] = useState<unknown>(null);

  // Lets the Verify loop stop asking the server once this screen is gone.
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const r: Report | null = report.data;
  const labels = useMemo(() => new Map((r?.evidenceFrames ?? []).map((f) => [f.index, f.label])), [r]);

  if (!id) {
    return (
      <>
        <Heading />
        <EmptyState title="No report selected">
          <p>
            Open a report from <Link href="/history/">History</Link> or <Link href="/">start a new audit</Link>.
          </p>
        </EmptyState>
      </>
    );
  }
  if (report.loading) {
    return (
      <>
        <Heading />
        <LoadingState label="Loading the report" />
      </>
    );
  }
  if (!r) {
    return (
      <>
        <Heading />
        <ReportProblem id={id} error={report.error} onRetry={report.retry} retried={report.retried} />
      </>
    );
  }

  const jobId = id;
  const selected = r.culprits.find((c) => c.rank === selectedRank) ?? null;
  const canAblate = r.culprits.length > 0 && r.culprits.some((c) => c.ablationDelta === null);

  const startReport = r;
  const verifyFinished: VerifyFinished | null =
    verifyDone === null ? null : { index: verifyDone.index, loaded: r !== verifyDone.from };

  // Verify = POST, then wait for the job (once per second) until it is
  // finished (isFinished checks the phase too: the job may keep status "done"
  // while it is verifying), then reload the report.
  async function verify(n: number) {
    if (verifying !== null) return; // the buttons are only aria-disabled
    setVerifying(n);
    setVerifyError(null);
    setVerifyDone(null);
    try {
      await verifyFix(jobId, n);
      const job = await pollUntil(() => getJob(jobId), isFinished, () => !mounted.current);
      if (job === null) return; // the screen has gone
      if (job.status === "error") throw new Error(job.error ?? "The verification run failed.");
      if (job.status === "cancelled") throw new Error("The audit was cancelled before the verification finished.");
      setVerifyDone({ index: n, from: startReport });
      report.retry();
    } catch (error) {
      setVerifyError({ index: n, message: errorMessage(error) });
    } finally {
      setVerifying(null);
    }
  }

  // Ablation re-captures several times; the Running screen already shows that
  // progress and comes back here when it is done.
  async function runAblation() {
    if (ablating) return; // the button is only aria-disabled
    setAblating(true);
    setAblateError(null);
    try {
      await ablate(jobId);
      router.push(`/run/?id=${encodeURIComponent(jobId)}`);
    } catch (error) {
      setAblateError(error);
      setAblating(false);
    }
  }

  return (
    <>
      <div className="page-head">
        <p className="kicker">Report</p>
        <h1>Audit report</h1>
      </div>

      <VerdictBanner report={r} />
      <SummaryList report={r} />

      <div className={`btn-row ${styles.actions}`}>
        <a className="btn" href={reportJsonUrl(jobId)} download={`flashtrace-${jobId}.json`}>
          Download report JSON
        </a>
        <a className="btn" href={reportHtmlUrl(jobId)} download={`flashtrace-${jobId}.html`}>
          Download HTML report
        </a>
      </div>
      {report.error !== null && (
        <ErrorState
          title="Could not reload the report"
          error={report.error}
          onRetry={report.retry}
          focusOnMount={report.retried}
        />
      )}

      <section className="section" aria-labelledby="timeline-heading">
        <h2 id="timeline-heading">Flashes per second</h2>
        <p className="lede">
          Bars count qualifying flashes in each whole second of the capture. The verdict checks every one-second
          window, so a violation can straddle two bars; shaded spans mark where one was found. The dashed line is the
          WCAG limit of three per second.
        </p>
        <TimelineChart bins={r.timeline} violations={r.violations} fps={r.settings.fps} />
      </section>

      <section className="section" aria-labelledby="evidence-heading">
        <h2 id="evidence-heading">Evidence frames</h2>
        <p className="lede">
          Still frames only. Open one to step through every captured frame by hand; nothing plays on its own.
        </p>
        <EvidenceStrip
          jobId={jobId}
          frames={r.evidenceFrames}
          fps={r.settings.fps}
          viewport={r.settings.viewport}
          onOpen={setDialogFrame}
        />
      </section>

      <section className="section" aria-labelledby="where-heading">
        <h2 id="where-heading">Where it flashes and what causes it</h2>
        <div className={styles.figureWrap}>
          <HeatmapView jobId={jobId} report={r} selected={selected} />
        </div>

        <h3 style={{ marginTop: 28 }}>Culprits</h3>
        <CulpritsTable report={r} selectedRank={selectedRank} onSelect={setSelectedRank} />
        {canAblate && (
          <div style={{ marginTop: 16 }}>
            <p className="hint" id="ablate-hint">
              Ablation hides each culprit in turn and measures again, which confirms the cause. It re-captures the
              page up to 6 times.
            </p>
            {/* aria-disabled keeps keyboard focus on the button while it works. */}
            <button
              type="button"
              className="btn"
              onClick={runAblation}
              aria-disabled={ablating}
              aria-describedby="ablate-hint"
            >
              {ablating ? "Starting ablation…" : "Re-run attribution with ablation"}
            </button>
            {ablateError !== null && (
              <ErrorState title="Could not start ablation" error={ablateError} onRetry={runAblation} focusOnMount />
            )}
          </div>
        )}
      </section>

      <section className="section" aria-labelledby="fixes-heading">
        <h2 id="fixes-heading">Suggested fixes</h2>
        <FixPanel
          report={r}
          verifying={verifying}
          verifyError={verifyError}
          verifyFinished={verifyFinished}
          onVerify={verify}
        />
      </section>

      <section className="section" aria-labelledby="violations-heading">
        <h2 id="violations-heading">Violations</h2>
        <ViolationsList report={r} onViewFrame={setDialogFrame} />
      </section>

      <section className="section" aria-labelledby="advisory-heading">
        <h2 id="advisory-heading">Advisories</h2>
        <p className="lede">Advisory - not a WCAG 2.3.1 criterion. These never change the verdict.</p>
        <AdvisoryList report={r} />
      </section>

      <section className="section" aria-labelledby="limits-heading">
        <h2 id="limits-heading">Limitations and assumptions</h2>
        <h3>Limitations</h3>
        <TextList items={r.limitations} empty="No limitations listed." />
        <h3>Assumptions</h3>
        <TextList items={r.assumptions} empty="No assumptions listed." />
        {r.capture.notes.length > 0 && (
          <>
            <h3>Capture notes</h3>
            <TextList items={r.capture.notes} empty="" />
          </>
        )}
        <p className="muted">{r.disclaimer}</p>
      </section>

      {dialogFrame !== null && (
        <FrameDialog
          key={dialogFrame}
          jobId={jobId}
          startIndex={dialogFrame}
          frameCount={r.capture.frameCount}
          fps={r.settings.fps}
          viewport={r.settings.viewport}
          labels={labels}
          onClose={() => setDialogFrame(null)}
        />
      )}
    </>
  );
}
