"use client";

import { motion } from "motion/react";
import type { Report } from "@/lib/api";
import { DUR, PAGE_CLOCK, SHIFT, useMotionTokens } from "@/lib/motion";
import DrawPath, { type Glyph } from "../motion/DrawPath";
import styles from "./Report.module.css";

// What each verdict does and does not mean. A PASS is never "compliant":
// it only means nothing was detected under the conditions that were tested.
const MEANING = {
  PASS: "No 2.3.1 violation detected under the tested conditions. This is not a certification that the page is safe for everyone.",
  FAIL: "In at least one one-second window, more than three qualifying flashes were detected.",
  INCONCLUSIVE: "INCONCLUSIVE is not a PASS. The reason above says what stopped a clear result; review the evidence by hand, or run the audit again once that is resolved.",
} as const;

const SEAL: Record<Report["verdict"], Glyph> = { PASS: "check", FAIL: "cross", INCONCLUSIVE: "question" };

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

/** The measured results that sit with the verdict. Real report fields only, never animated (no count-up: MOTION.md principle 5). */
function keyFigures(report: Report): { label: string; value: number }[] {
  const most = (pick: (b: Report["timeline"][number]) => number) => Math.max(0, ...report.timeline.map(pick));
  return [
    { label: "Violations", value: report.violations.length },
    { label: "Most general flashes in a whole second", value: most((b) => b.generalFlashes) },
    { label: "Most red flashes in a whole second", value: most((b) => b.redFlashes) },
    { label: "Frames captured", value: report.capture.frameCount },
    { label: "Culprits", value: report.culprits.length },
  ];
}

interface Props {
  report: Report;
  /** True on the first arrival of this job's report: the group enters once; a reload never replays it. */
  enter: boolean;
}

export default function VerdictBanner({ report, enter }: Props) {
  const { verdict, capture } = report;
  const note = nonDeterminismNote(report);
  const m = useMotionTokens();
  // Motion (web/MOTION.md 3.3): the verdict is the headline, revealed once:
  // the banner, its key figures and the notice rise 8 px and fade in together
  // (DUR.emphasis, 420 ms); the seal draws from +120 ms (DUR.base), so it is
  // complete at 360 ms, inside the banner's rise.
  const animate = enter && !m.reduce;
  return (
    <motion.div
      {...PAGE_CLOCK}
      data-arrive=""
      initial={animate ? { opacity: 0, y: SHIFT.s } : false}
      animate={{ opacity: 1, y: 0 }}
      transition={m.tween(DUR.emphasis)}
    >
      <section className={`${styles.banner} ${styles[verdict]}`} aria-labelledby="verdict-heading">
        <div className={styles.verdictHead}>
          <DrawPath glyph={SEAL[verdict]} ring size={48} strokeWidth={2} draw={animate} delay={0.12} duration={DUR.base} className={styles.seal} />
          <h2 id="verdict-heading" className={styles.verdictLine}>
            <span className="visually-hidden">Verdict: </span>
            {verdict}
          </h2>
        </div>
        <p className={styles.reason}>{report.reason}</p>
        <dl className={styles.figures}>
          {keyFigures(report).map((f) => (
            <div key={f.label}>
              <dt>{f.label}</dt>
              <dd className="num">{f.value}</dd>
            </div>
          ))}
        </dl>
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
    </motion.div>
  );
}
