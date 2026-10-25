"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import { flushSync } from "react-dom";
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
import { GATE_MS, LargeAreaGateContext, useTrailingGate } from "@/lib/motion";
import { onPageClock, pollUntil, useLoad } from "@/lib/usePolling";
import { useSafeToggle } from "@/lib/useSafeToggle";
import { EmptyState, ErrorState, LoadingState, errorMessage } from "../StatusViews";
import CulpritsTable from "./CulpritsTable";
import EvidenceStrip, { type OpenRequest } from "./EvidenceStrip";
import { AdvisoryList, TextList, ViolationsList } from "./FindingsLists";
import FixPanel, { type VerifyFinished } from "./FixPanel";
import FrameDialog from "./FrameDialog";
import HeatmapView from "./HeatmapView";
import styles from "./Report.module.css";
import SummaryList from "./SummaryList";
import TimelineChart from "./TimelineChart";
import VerdictBanner from "./VerdictBanner";

/**
 * Job ids whose report entrance has played in this tab (web/MOTION.md 3.3:
 * entrances run once per job id, on the first data arrival; a reload after
 * Verify, or coming back to the same report, never replays them).
 */
const entered = new Set<string>();

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
      <EmptyState title="This audit has not finished yet" illustration="frame">
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

  const [selection, setSelection] = useState<{ rank: number | null; instant: boolean }>({ rank: null, instant: false });
  const selectedRank = selection.rank;
  const [dialog, setDialog] = useState<OpenRequest | null>(null);
  // SAFETY: opening the viewer veils the whole page, and each open plus close
  // can complete two flashes (the veil over a black evidence thumbnail, the
  // viewer's own frame). Opens are at least 1 s apart, and the viewer also stays
  // shut for 1 s after it finished closing (the self-audit's F1 at 390 px found
  // two open-close cycles inside one second); a click inside that time opens the
  // viewer when it ends instead of being lost (leading plus trailing). Closing
  // is never gated.
  const openGate = useTrailingGate(GATE_MS.dialogOpen);
  // flushSync: a deferred open runs from a timer; it commits in that same frame.
  const openFrame = (request: OpenRequest) => openGate.run(() => flushSync(() => setDialog(request)));
  const closeFrame = () => {
    setDialog(null);
    openGate.restart();
  };
  // SAFETY: the heatmap overlay and the data-table disclosure change the same
  // cells, so they share one 350 ms gate (MOTION.md 3.3, section 6).
  const largeArea = useSafeToggle(GATE_MS.largeArea);
  // Decided once, when this job's report first arrives.
  const [enterFor, setEnterFor] = useState<string | null>(null);
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

  // The first arrival of a job's report plays the entrance; record it so it never replays.
  const arrivedId = r !== null ? id : null;
  useEffect(() => {
    if (arrivedId === null || entered.has(arrivedId)) return;
    entered.add(arrivedId);
    const frame = requestAnimationFrame(() => setEnterFor(arrivedId));
    return () => cancelAnimationFrame(frame);
  }, [arrivedId]);

  if (!id) {
    return (
      <>
        <Heading />
        <EmptyState title="No report selected" illustration="frame">
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
  // The entrance plays for a report that has not played it yet in this tab
  // (checked at render so the first painted frame is already the hidden state).
  const enter = enterFor === jobId || !entered.has(jobId);
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
      const polled = await pollUntil(() => getJob(jobId), isFinished, () => !mounted.current);
      if (polled === null) return; // the screen has gone
      const job = polled.value;
      if (job.status === "error") throw new Error(job.error ?? "The verification run failed.");
      if (job.status === "cancelled") throw new Error("The audit was cancelled before the verification finished.");
      // On the page clock, like every network result (lib/usePolling onPageClock), and in one
      // commit: the busy state ends with the result, not in a React task of its own a frame
      // later (two self-audit captures of F7 differed by that frame under load).
      await onPageClock(() => {
        setVerifying(null);
        setVerifyDone({ index: n, from: startReport });
        report.retry();
      }, polled.startedAt);
    } catch (error) {
      setVerifyError({ index: n, message: errorMessage(error) });
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
    <LargeAreaGateContext.Provider value={largeArea}>
      <div className="page-head">
        <p className="kicker">Report</p>
        <h1>Audit report</h1>
      </div>

      <VerdictBanner report={r} enter={enter} />
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
          onOpen={openFrame}
        />
      </section>

      <section className="section" aria-labelledby="where-heading">
        <h2 id="where-heading">Where it flashes and what causes it</h2>
        <div className={styles.figureWrap}>
          <HeatmapView jobId={jobId} report={r} selected={selected} instant={selection.instant} />
        </div>

        <h3 className={styles.subhead}>Culprits</h3>
        <CulpritsTable
          report={r}
          selectedRank={selectedRank}
          onSelect={(rank, instant) => setSelection({ rank, instant })}
        />
        {canAblate && (
          <div className={styles.ablate}>
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
        <ViolationsList report={r} onViewFrame={openFrame} />
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

      {dialog !== null && (
        <FrameDialog
          key={`${dialog.index}`}
          jobId={jobId}
          startIndex={dialog.index}
          frameCount={r.capture.frameCount}
          fps={r.settings.fps}
          viewport={r.settings.viewport}
          labels={labels}
          origin={dialog.origin}
          keyboard={dialog.keyboard}
          opener={dialog.opener}
          onClose={closeFrame}
        />
      )}
    </LargeAreaGateContext.Provider>
  );
}
