"use client";

import { AnimatePresence, motion } from "motion/react";
import { useEffect, useRef, useState } from "react";
import type { Fix, FixKind, Measurement, Report } from "@/lib/api";
import { DUR, PAGE_CLOCK, SHIFT, useMotionTokens } from "@/lib/motion";
import CrossfadeLabel from "../motion/CrossfadeLabel";
import DrawPath from "../motion/DrawPath";
import styles from "./Report.module.css";

const KIND_LABEL: Record<FixKind, string> = {
  rate: "Slow it down (rate)",
  luminance: "Reduce the contrast (luminance)",
  red: "Remove saturated red (red)",
  area: "Make it smaller (area)",
};

/** The latest verification that finished, and whether the reloaded report (with its new After column) has arrived yet. */
export interface VerifyFinished {
  index: number;
  loaded: boolean;
}

interface Props {
  report: Report;
  /** Index of the fix being verified right now, if any. */
  verifying: number | null;
  verifyError: { index: number; message: string } | null;
  verifyFinished: VerifyFinished | null;
  onVerify: (index: number) => void;
}

/**
 * The words for the fix's live status line. A screen reader user hears that
 * verification started; this makes sure they also hear that it ended and
 * what it found, not just a status line going quiet.
 */
function statusText(fix: Fix, i: number, { verifying, verifyError, verifyFinished }: Omit<Props, "report" | "onVerify">): string {
  if (verifying === i) return "Re-capturing the page with this CSS injected. Checking once per second.";
  if (verifyError?.index === i) return `Verification failed: ${verifyError.message}`;
  if (verifyFinished?.index !== i) return "";
  if (!verifyFinished.loaded) return "Verification finished. Loading the new measurements.";
  const a = fix.after;
  if (!a) return "Verification finished, but the server sent no new measurements for this fix.";
  return (
    `Verification finished: verdict after the fix is ${a.verdict}; general flashes max ${a.generalMaxRatePerSec} per second, ` +
    `red ${a.redMaxRatePerSec} per second. ${fix.verified ? "Verified." : "Not verified."}`
  );
}

/**
 * An After value (web/MOTION.md 3.3). When a verification brings a new value
 * it fades in (opacity, 4 px rise) beside the unchanged Before value. It never
 * ticks from Before to After: the values in between were never measured.
 */
function AfterValue({ text }: { text: string }) {
  const m = useMotionTokens();
  return (
    <AnimatePresence initial={false} mode="popLayout">
      <motion.span
        {...PAGE_CLOCK}
        key={text}
        style={{ display: "inline-block" }}
        initial={m.from({ opacity: 0, y: SHIFT.xs })}
        animate={{ opacity: 1, y: 0 }}
        transition={m.tween(DUR.base)}
      >
        {text}
      </motion.span>
    </AnimatePresence>
  );
}

function MeasureTable({ fix, n }: { fix: Fix; n: number }) {
  const rows: { label: string; get: (m: Measurement) => string }[] = [
    { label: "Verdict", get: (m) => m.verdict },
    { label: "General flashes, max per second", get: (m) => String(m.generalMaxRatePerSec) },
    { label: "Red flashes, max per second", get: (m) => String(m.redMaxRatePerSec) },
    { label: "Qualifying general flashes", get: (m) => String(m.qualifyingGeneral) },
    { label: "Qualifying red flashes", get: (m) => String(m.qualifyingRed) },
    // A re-run counts as verified only under deterministic capture (docs/SPEC.md 7).
    { label: "Deterministic capture", get: (m) => (m.deterministic ? "Yes" : "No") },
  ];
  return (
    <div className="table-wrap">
      <table className={styles.measure}>
        <caption className="visually-hidden">Fix {n} measurements before and after</caption>
        <thead>
          <tr>
            <th scope="col">Measure</th>
            <th scope="col" className="numeric">
              Before
            </th>
            <th scope="col" className="numeric">
              After
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const before = row.get(fix.before);
            const after = fix.after ? row.get(fix.after) : null;
            // A persistent tint marks a value the fix changed (not a flash: the darker side stays above 0.80).
            const changed = after !== null && after !== before;
            return (
              <tr key={row.label}>
                <th scope="row">{row.label}</th>
                <td className="numeric">{before}</td>
                <td className={`numeric ${changed ? styles.changed : ""}`}>
                  <AfterValue text={after ?? "not measured"} />
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

const COPIED_FOR_MS = 2000;

function CopyIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <rect x="8" y="8" width="12" height="12" rx="1.5" fill="none" stroke="currentColor" strokeWidth="2" />
      <path d="M16 8V5.5A1.5 1.5 0 0 0 14.5 4h-9A1.5 1.5 0 0 0 4 5.5v9A1.5 1.5 0 0 0 5.5 16H8" fill="none" stroke="currentColor" strokeWidth="2" />
    </svg>
  );
}

/**
 * Copy CSS (web/MOTION.md 3.3): the icon crossfades to a check and the label
 * reads "Copied" in a fixed width, then reverts once after 2 s (one revert,
 * nothing periodic). A click while "Copied" shows copies again and restarts
 * the 2 s without replaying the morph. The status line is a polite live region.
 */
function CopyButton({ text, label }: { text: string; label: string }) {
  const [status, setStatus] = useState("");
  const [copied, setCopied] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  useEffect(() => () => clearTimeout(timer.current), []);
  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      setStatus("Copied to the clipboard.");
      setCopied(true);
      clearTimeout(timer.current);
      timer.current = setTimeout(() => setCopied(false), COPIED_FOR_MS);
    } catch {
      setStatus("Copy failed. Select the CSS and copy it by hand.");
    }
  }
  return (
    <>
      <button
        type="button"
        className={`btn btn-small ${styles.copyButton}`}
        onClick={copy}
        aria-label={`${copied ? "Copied CSS" : "Copy CSS"} for ${label}`}
      >
        <span className={styles.copyIcon}>
          <CrossfadeLabel value={copied ? "done" : "copy"} scaleFrom={0.8}>
            {copied ? <DrawPath glyph="check" size={14} strokeWidth={2.5} draw={false} /> : <CopyIcon />}
          </CrossfadeLabel>
        </span>
        <span className={styles.copyText}>{copied ? "Copied" : "Copy CSS"}</span>
      </button>
      <span className={styles.status} aria-live="polite">
        {status}
      </span>
    </>
  );
}

/**
 * The Verified badge (web/MOTION.md 3.3): when a verification flips it, "○ Not
 * verified" crossfades to a drawn check and "Verified", and the badge settles
 * from scale 0.96. One .badge element throughout, so its text is always one label.
 */
function VerifiedBadge({ verified }: { verified: boolean }) {
  const m = useMotionTokens();
  const [first] = useState(verified);
  const changed = verified !== first;
  return (
    <motion.span
      className={`badge ${verified ? "badge-ok" : "badge-no"}`}
      initial={false}
      animate={{ scale: changed && !m.reduce ? [0.96, 1] : 1 }}
      transition={m.tween(DUR.base)}
    >
      <CrossfadeLabel value={verified ? "yes" : "no"}>
        {verified ? (
          <DrawPath glyph="check" size={13} strokeWidth={3} draw={changed} duration={DUR.base} />
        ) : (
          <span aria-hidden="true">○</span>
        )}
        {verified ? "Verified" : "Not verified"}
      </CrossfadeLabel>
    </motion.span>
  );
}

export default function FixPanel({ report, verifying, verifyError, verifyFinished, onVerify }: Props) {
  if (report.fixes.length === 0) {
    return (
      <p>
        {report.culprits.length === 0
          ? "No fixes: there are no culprits to fix."
          : "No CSS fixes were suggested for these culprits."}
      </p>
    );
  }
  const selectorOf = (rank: number) => report.culprits.find((c) => c.rank === rank)?.selector ?? `rank ${rank}`;

  return (
    <ol className={styles.fixes}>
      {report.fixes.map((fix, i) => {
        const n = i + 1;
        const headingId = `fix-${n}-title`;
        const isArea = fix.kind === "area";
        return (
          <li key={i} className={styles.fix}>
            <div className={styles.fixHead}>
              <h3 id={headingId}>
                Fix {n}: {KIND_LABEL[fix.kind]}
              </h3>
              <VerifiedBadge verified={fix.verified} />
            </div>
            {fix.after?.verdict === "PASS" && !fix.verified && (
              <p className={styles.why}>
                Not verified: the re-run passed, but its capture was not deterministic, so frames may have been
                missed.
              </p>
            )}
            <p className="muted">
              For culprit {fix.culprit}: <code>{selectorOf(fix.culprit)}</code>
            </p>
            <p>{fix.rationale}</p>
            {isArea && (
              <p>
                <strong>Not auto-applied.</strong> An area fix changes the layout, so FlashTrace does not inject or
                verify it. Apply it yourself and run a new audit.
              </p>
            )}
            <pre className={styles.code}>
              <code>{fix.css}</code>
            </pre>
            <div className="btn-row">
              <CopyButton text={fix.css} label={`fix ${n}`} />
            </div>
            <MeasureTable fix={fix} n={n} />
            {!isArea && (
              <div className="btn-row">
                {/* aria-disabled, not disabled: a disabled button loses keyboard
                    focus (it drops to <body>), so the user would have to Tab
                    back from the top of the page. onClick ignores it while busy. */}
                {/* The label stays "Verifying…" until the reloaded report has
                    arrived, so it changes once ("Verifying…" -> "Verify again"),
                    never through "Verify this fix" for a frame in between. */}
                <button
                  type="button"
                  className="btn"
                  onClick={() => {
                    if (verifying === null) onVerify(i);
                  }}
                  aria-disabled={verifying !== null}
                  aria-describedby={`fix-${n}-status`}
                >
                  {verifying === i || (verifyFinished?.index === i && !verifyFinished.loaded)
                    ? "Verifying…"
                    : fix.verified
                      ? "Verify again"
                      : "Verify this fix"}
                </button>
                <span id={`fix-${n}-status`} className={styles.status} role="status">
                  {statusText(fix, i, { verifying, verifyError, verifyFinished })}
                </span>
              </div>
            )}
          </li>
        );
      })}
    </ol>
  );
}
