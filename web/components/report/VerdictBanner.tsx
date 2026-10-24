import type { Report } from "@/lib/api";
import { VERDICT_GLYPH } from "../VerdictTag";
import styles from "./Report.module.css";

// What each verdict does and does not mean. A PASS is never "compliant":
// it only means nothing was detected under the conditions that were tested.
const MEANING = {
  PASS: "No 2.3.1 violation detected under the tested conditions. This is not a certification that the page is safe for everyone.",
  FAIL: "In at least one one-second window, more than three qualifying flashes were detected.",
  INCONCLUSIVE: "INCONCLUSIVE is not a PASS. The reason above says what stopped a clear result; review the evidence by hand, or run the audit again once that is resolved.",
} as const;

/**
 * What went wrong with the capture's clock, or null when nothing did.
 * Decided by the capture itself, never by the verdict: an INCONCLUSIVE can
 * also come from a capture error or too few frames (docs/SPEC.md 6.4), and
 * report.reason already explains those. Only "realtime" means frames were
 * recorded in real time; a stepped capture (waapi_step / virtual_time) is
 * non-deterministic when something escaped the controlled clock (6.2).
 */
export function nonDeterminismNote(report: Report): string | null {
  const { strategy, deterministic } = report.capture;
  if (strategy === "none") return null; // nothing was captured; the reason says why
  if (strategy === "realtime") {
    return "Frames were recorded in real time, so flash timing can differ between runs and frames may have been dropped. Treat any result from this capture as indicative only.";
  }
  if (deterministic) return null;
  return "Something on the page animated outside the controlled animation clock (for example a video that did not seek in time, or a change between two shots of the same moment), so two captures of this page may differ and flashes may have been missed. Treat any result from this capture as indicative only.";
}

export default function VerdictBanner({ report }: { report: Report }) {
  const { verdict, capture } = report;
  const note = nonDeterminismNote(report);
  return (
    <>
      <section className={`${styles.banner} ${styles[verdict]}`} aria-labelledby="verdict-heading">
        <h2 id="verdict-heading" className={styles.verdictLine}>
          <span className="verdict">
            <span className="verdict-glyph" aria-hidden="true">
              {VERDICT_GLYPH[verdict]}
            </span>
            <span className="visually-hidden">Verdict: </span>
            {verdict}
          </span>
        </h2>
        <p className={styles.reason}>{report.reason}</p>
        <p className={styles.meaning}>{MEANING[verdict]}</p>
      </section>

      {note !== null && (
        <div className="notice" role="note" aria-labelledby="nondet-heading">
          <p id="nondet-heading">
            <strong>
              <span aria-hidden="true">⚠ </span>Non-deterministic capture
            </strong>
          </p>
          <p>{note}</p>
          {capture.fallbackReason && <p>Why: {capture.fallbackReason}</p>}
          {capture.uncontrollable.length > 0 && (
            <p>Could not be clock-controlled: {capture.uncontrollable.join(", ")}.</p>
          )}
        </div>
      )}
    </>
  );
}
