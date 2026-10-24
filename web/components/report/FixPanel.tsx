"use client";

import { useState } from "react";
import type { Fix, FixKind, Measurement, Report } from "@/lib/api";
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
          {rows.map((row) => (
            <tr key={row.label}>
              <th scope="row">{row.label}</th>
              <td className="numeric">{row.get(fix.before)}</td>
              <td className="numeric">{fix.after ? row.get(fix.after) : "not measured"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function CopyButton({ text, label }: { text: string; label: string }) {
  const [status, setStatus] = useState("");
  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      setStatus("Copied to the clipboard.");
    } catch {
      setStatus("Copy failed. Select the CSS and copy it by hand.");
    }
  }
  return (
    <>
      <button type="button" className="btn btn-small" onClick={copy} aria-label={`Copy CSS for ${label}`}>
        Copy CSS
      </button>
      <span className={styles.status} aria-live="polite">
        {status}
      </span>
    </>
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
              <span className={`badge ${fix.verified ? "badge-ok" : "badge-no"}`}>
                <span aria-hidden="true">{fix.verified ? "✓" : "○"}</span>
                {fix.verified ? "Verified" : "Not verified"}
              </span>
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
                <button
                  type="button"
                  className="btn"
                  onClick={() => {
                    if (verifying === null) onVerify(i);
                  }}
                  aria-disabled={verifying !== null}
                  aria-describedby={`fix-${n}-status`}
                >
                  {verifying === i ? "Verifying…" : fix.verified ? "Verify again" : "Verify this fix"}
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
