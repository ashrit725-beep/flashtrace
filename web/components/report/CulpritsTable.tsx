"use client";

import type { Culprit, Report } from "@/lib/api";
import { percent } from "@/lib/format";
import { useSafeToggle } from "@/lib/useSafeToggle";
import styles from "./Report.module.css";

interface Props {
  report: Report;
  selectedRank: number | null;
  onSelect: (rank: number | null) => void;
}

/** Why the table is empty, in the user's terms. */
function emptyMessage(report: Report): string {
  const mode = report.settings.attribute;
  if (mode === "none") return "No attribution was run (mode: none). Start a new audit with attribution set to bounding boxes or ablation to name the elements that flash.";
  if (report.violations.length === 0) return `Attribution ran (mode: ${mode}) and found no flashing elements to name.`;
  return `Attribution ran (mode: ${mode}) but could not match the flashing area to an element. It may be drawn inside a canvas, video or cross-origin iframe.`;
}

function animationText(c: Culprit): string {
  if (!c.animation) return "none found";
  const duration = c.animation.duration === null ? "unknown duration" : `${c.animation.duration} ms`;
  return `${c.animation.name || "(unnamed)"}, ${duration}`;
}

/**
 * The minimal culprit set is built by ablation (docs/SPEC.md section 7). With
 * no ablation result there is no set, and "No" would wrongly suggest the
 * element had been ruled out.
 */
function minimalSetText(c: Culprit): string {
  if (c.ablationDelta === null) return "not measured";
  return c.inMinimalSet ? "Yes, in minimal set" : "No";
}

// Each row has a real toggle button (aria-pressed) so keyboard and screen
// reader users can select a culprit; clicking anywhere on the row is a mouse
// convenience that does the same thing. The button sits in the third column
// so pressing it on a phone does not scroll the table away from the
// element's name.
export default function CulpritsTable({ report, selectedRank, onSelect }: Props) {
  // SAFETY: one throttle for every row and button here, so neither a held
  // Enter nor fast clicking (on one row or alternating rows) can make the
  // outline on the heatmap blink faster than 3 Hz.
  const safe = useSafeToggle();

  if (report.culprits.length === 0) return <p>{emptyMessage(report)}</p>;

  const toggle = (rank: number) => safe.run(() => onSelect(selectedRank === rank ? null : rank));

  return (
    <>
      {/* The explanation sits outside the scrolling box, so it never scrolls
          sideways with the table on a phone. */}
      <p className="hint" id="culprits-help">
        Elements that overlap the flashing area, most likely first. Select one to outline it on the heatmap.
      </p>
      <div className={`table-wrap ${styles.culprits}`}>
        <table aria-describedby="culprits-help">
          <caption className="visually-hidden">Culprits, most likely first</caption>
          <thead>
            <tr>
              <th scope="col">Rank</th>
              <th scope="col">Element</th>
              <th scope="col">Heatmap</th>
              <th scope="col">Node</th>
              <th scope="col">Animation</th>
              <th scope="col" className="numeric">
                Overlap
              </th>
              <th scope="col">Ablation delta</th>
              <th scope="col">Minimal set</th>
            </tr>
          </thead>
          <tbody>
            {report.culprits.map((c) => {
              const isSelected = selectedRank === c.rank;
              return (
                <tr
                  key={c.rank}
                  className={`${styles.culpritRow} ${isSelected ? styles.selectedRow : ""}`}
                  onClick={() => toggle(c.rank)}
                >
                  <td className="num">{c.rank}</td>
                  <td>
                    <code>{c.selector}</code>
                  </td>
                  <td>
                    <button
                      type="button"
                      className="btn btn-small"
                      aria-pressed={isSelected}
                      aria-label={`Outline culprit ${c.rank} (${c.selector}) on the heatmap`}
                      onKeyDown={safe.onKeyDown}
                      onClick={(event) => {
                        event.stopPropagation(); // the row's own click would toggle twice
                        toggle(c.rank);
                      }}
                    >
                      Outline
                    </button>
                  </td>
                  <td>{c.nodeType.toLowerCase()}</td>
                  <td>{animationText(c)}</td>
                  <td className="numeric">{percent(c.overlap)}</td>
                  <td>{c.ablationDelta === null ? "not measured" : `${c.ablationDelta} fewer flashes`}</td>
                  <td>{minimalSetText(c)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </>
  );
}
