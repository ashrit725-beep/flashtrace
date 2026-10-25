"use client";

import { motion } from "motion/react";
import { useEffect, useId, useRef, useState, type PointerEvent } from "react";
import type { TimelineBin, Violation } from "@/lib/api";
import { frameTime } from "@/lib/format";
import { EASE, DUR, PAGE_CLOCK, staggerDelay, useInViewArrival, useLargeAreaGate, useMotionTokens } from "@/lib/motion";
import styles from "./Report.module.css";

/** WCAG 2.3.1: no more than three flashes in any one-second period. */
const LIMIT = 3;
const HEIGHT = 240;
const M = { top: 28, bottom: 46, left: 52 };
/** Wide charts label the limit line at its right end; narrow ones above the plot. */
const LABEL_ROOM = 176;

// Motion (web/MOTION.md 3.3, section 8), once, when the chart first comes on
// screen. The WCAG limit is the reference every bar is judged against, like the
// axes and the grid, so it is there from the first frame. The bars rise against
// it from the baseline (DUR.base on ease-out, so they answer at once, staggered
// by at most 120 ms in total), then the windows over the limit are marked (the
// spans fade in from +240 ms, DUR.quick). Everything ends by 400 ms. A bar
// being drawn is not read as a value; the data table and the readout give the
// exact counts.
const BAR = { duration: DUR.base, stagger: 0.12 } as const;
const SPANS = { delay: 0.24 } as const;

interface Props {
  bins: TimelineBin[];
  violations: Violation[];
  fps: number;
}

// The SVG is drawn at the container's real pixel width (not scaled through a
// viewBox) so its 12 px labels stay 12 px on a phone as well as a desktop.
function useWidth<T extends HTMLElement>(fallback: number) {
  const ref = useRef<T>(null);
  const [width, setWidth] = useState(fallback);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const observer = new ResizeObserver(([entry]) => setWidth(Math.floor(entry.contentRect.width)));
    observer.observe(el);
    return () => observer.disconnect();
  }, []);
  return [ref, width] as const;
}

/**
 * Why the chart needs more than bars: each bar counts flashes in one WHOLE
 * second (docs/SPEC.md 5.8), but the verdict slides a one-second window over
 * every frame (5.7). Two flashes late in second 0 and two early in second 1
 * give bars of 2 and 2, yet a window across the boundary holds 4: a FAIL with
 * no bar over the line. So every violation is drawn as a shaded span of time
 * and named in the text alternative and the table.
 */
interface Span {
  n: number;
  /** seconds */
  from: number;
  to: number;
  v: Violation;
}

const spansOf = (violations: Violation[], fps: number): Span[] =>
  violations.map((v, i) => ({ n: i + 1, from: v.start / fps, to: (v.end + 1) / fps, v }));

const spanText = (s: Span, fps: number) =>
  `violation ${s.n}, ${s.v.type} flashes up to ${s.v.maxRatePerSec} per second, from ${frameTime(s.v.start, fps)} s to ${frameTime(s.v.end, fps)} s`;

function summary(bins: TimelineBin[], spans: Span[], fps: number): string {
  const n = bins.length;
  const gMax = Math.max(0, ...bins.map((b) => b.generalFlashes));
  const rMax = Math.max(0, ...bins.map((b) => b.redFlashes));
  const windows =
    spans.length === 0
      ? `No one-second window, checked at every frame, had more than ${LIMIT} flashes.`
      : `Checking every one-second window found ${spans.length} violation${spans.length === 1 ? "" : "s"}: ` +
        `${spans.map((s) => spanText(s, fps)).join("; ")}.`;
  return (
    `Bar chart of flashes counted in each whole second, over ${n} second${n === 1 ? "" : "s"}. ` +
    `The most in one whole second: ${gMax} general and ${rMax} red. ${windows} ` +
    `The WCAG limit is ${LIMIT} per second.`
  );
}

export default function TimelineChart({ bins, violations, fps }: Props) {
  const [boxRef, width] = useWidth<HTMLDivElement>(720);
  const [showTable, setShowTable] = useState(false);
  // SAFETY: showing the table pushes everything below it (including the
  // black-and-white evidence frames and the heatmap) up and down; keep that
  // under 3 Hz. The heatmap overlay changes the same cells, so both controls
  // share the report's one large-area gate (MOTION.md 3.3).
  const safe = useLargeAreaGate();
  const m = useMotionTokens();
  const [arriveRef, arrival] = useInViewArrival<HTMLDivElement>(!m.reduce);
  const [hover, setHover] = useState<number | null>(null);
  const uid = useId().replace(/:/g, "");
  const hatchId = `${uid}hatch`;

  if (bins.length === 0) {
    return <p className="muted">No timeline: the capture contained no frames to count.</p>;
  }

  const spans = spansOf(violations, fps);
  // Pairs of bars only when there is something red to show; otherwise one
  // bar centred on its second, so bars line up with their labels.
  const hasRed = bins.some((b) => b.redFlashes > 0);

  // ---- scales: plain arithmetic, no chart library
  const wide = width >= 560;
  const plotW = Math.max(120, width - M.left - (wide ? LABEL_ROOM : 16));
  const plotH = HEIGHT - M.top - M.bottom;
  const peak = Math.max(...bins.map((b) => Math.max(b.generalFlashes, b.redFlashes)));
  const yMax = Math.max(LIMIT + 1, Math.ceil(peak) + 1);
  const y = (v: number) => M.top + plotH - (v / yMax) * plotH;
  const band = plotW / bins.length; // one band per second
  const x = (seconds: number) => M.left + Math.min(bins.length, Math.max(0, seconds)) * band;
  const barW = Math.max(2, Math.min(18, band * 0.36));
  const yStep = yMax <= 8 ? 1 : Math.ceil(yMax / 6);
  const yTicks = Array.from({ length: Math.floor(yMax / yStep) + 1 }, (_, i) => i * yStep);
  const xEvery = Math.max(1, Math.ceil(28 / band)); // keep x labels at least ~28 px apart

  // ---- one-shot entrance, driven by the in-view arrival. The chart starts in
  // its final state and is hidden only after the observer reported it off
  // screen (MOTION.md principle 7); it animates only when it then comes on screen.
  const waiting = arrival === "waiting";
  const timing = (duration: number, delay: number) =>
    arrival === "play" ? m.tween(duration, { delay, ease: EASE.out }) : { duration: 0 };

  // ---- pointer readout: a static column tint and one line of text (no motion, no gate needed)
  function onPointer(event: PointerEvent<SVGSVGElement>) {
    const box = event.currentTarget.getBoundingClientRect();
    const second = Math.floor((event.clientX - box.left - M.left) / band);
    setHover(second >= 0 && second < bins.length ? second : null);
  }
  const hovered = hover === null ? null : bins[hover];

  return (
    <div ref={arriveRef}>
      <ul className={styles.legend} aria-hidden="true">
        <li>
          <span className={`${styles.swatch} ${styles.swatchGeneral}`} /> General flashes
        </li>
        <li>
          <span className={`${styles.swatch} ${styles.swatchRed}`} /> Red flashes (hatched)
          {hasRed ? "" : ": none recorded"}
        </li>
        <li>
          <span className={`${styles.swatch} ${styles.swatchLimit}`} /> WCAG limit: {LIMIT} per second
        </li>
        {spans.length > 0 && (
          <li>
            <span className={`${styles.swatch} ${styles.swatchSpan}`} /> Violation (a one-second window over the limit)
          </li>
        )}
      </ul>

      <div ref={boxRef} className={styles.chartBox}>
        <svg
          className={styles.chart}
          width={width}
          height={HEIGHT}
          role="img"
          aria-label={summary(bins, spans, fps)}
          onPointerMove={onPointer}
          onPointerLeave={() => setHover(null)}
        >
          <defs>
            <pattern id={hatchId} width="5" height="5" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
              <rect width="2" height="5" fill="var(--chart-red)" />
            </pattern>
          </defs>

          {/* pointer readout column: drawn first, under everything */}
          {hover !== null && (
            <rect className={styles.hoverColumn} x={M.left + band * hover} y={M.top} width={band} height={plotH} />
          )}

          {/* violation spans first, so grid and bars draw on top of the shading */}
          {spans.map((s) => (
            <motion.g
              {...PAGE_CLOCK}
              key={s.n}
              data-arrive=""
              initial={false}
              animate={{ opacity: waiting ? 0 : 1 }}
              transition={timing(DUR.quick, SPANS.delay)}
            >
              <rect x={x(s.from)} y={M.top} width={Math.max(1, x(s.to) - x(s.from))} height={plotH} fill="var(--fail-bg)" />
              <line x1={x(s.from)} x2={x(s.to)} y1={M.top} y2={M.top} stroke="var(--fail)" strokeWidth="3" />
            </motion.g>
          ))}

          {/* horizontal grid + y tick labels */}
          {yTicks.map((v) => (
            <g key={v}>
              <line x1={M.left} x2={M.left + plotW} y1={y(v)} y2={y(v)} stroke="var(--chart-grid)" shapeRendering="crispEdges" />
              <text x={M.left - 8} y={y(v) + 4} textAnchor="end">
                {v}
              </text>
            </g>
          ))}

          {/* bars: general solid, red hatched, so the series differ by more than colour. Square
              ends, no rounding: a bar's top is its value. Each grows from the baseline once. */}
          {bins.map((b, i) => {
            const cx = M.left + band * i + band / 2;
            return (
              <g key={b.t}>
                <motion.g
                  data-arrive=""
                  style={{ originY: 1 }}
                  initial={false}
                  animate={{ scaleY: waiting ? 0 : 1 }}
                  transition={timing(BAR.duration, staggerDelay(i, bins.length, BAR.stagger))}
                >
                  <rect
                    x={hasRed ? cx - barW - 1 : cx - barW / 2}
                    y={y(b.generalFlashes)}
                    width={barW}
                    height={y(0) - y(b.generalFlashes)}
                    fill="var(--chart-general)"
                  />
                  {hasRed &&
                    (b.redFlashes > 0 ? (
                      <rect
                        x={cx + 1}
                        y={y(b.redFlashes)}
                        width={barW}
                        height={y(0) - y(b.redFlashes)}
                        fill={`url(#${hatchId})`}
                        stroke="var(--chart-red)"
                      />
                    ) : (
                      // A zero still gets a 1 px stub, so the pair reads as centred on its label.
                      <rect x={cx + 1} y={y(0) - 1} width={barW} height={1} fill="var(--chart-red)" />
                    ))}
                </motion.g>
                {i % xEvery === 0 && (
                  <text x={cx} y={M.top + plotH + 18} textAnchor="middle">
                    {b.t}
                  </text>
                )}
              </g>
            );
          })}

          {/* axes */}
          <line x1={M.left} x2={M.left + plotW} y1={y(0)} y2={y(0)} stroke="var(--ink)" strokeWidth="1" shapeRendering="crispEdges" />
          <line x1={M.left} x2={M.left} y1={M.top} y2={y(0)} stroke="var(--ink)" strokeWidth="1" shapeRendering="crispEdges" />

          {/* the threshold every one-second window is judged against */}
          <line
            x1={M.left}
            x2={M.left + plotW}
            y1={y(LIMIT)}
            y2={y(LIMIT)}
            stroke="var(--fail)"
            strokeWidth="2"
            strokeDasharray="7 5"
          />
          <text
            className={styles.limitLabel}
            x={wide ? M.left + plotW + 8 : M.left + plotW}
            y={wide ? y(LIMIT) + 4 : M.top - 12}
            textAnchor={wide ? "start" : "end"}
          >
            WCAG limit: {LIMIT} per second
          </text>

          {/* Span labels last, on top of the bars (the paper-coloured outline
              keeps them readable). A span that starts in the right half is
              labelled from its right end, so the text stays inside the chart. */}
          {spans.map((s) => {
            const fromRight = x(s.from) > M.left + plotW / 2;
            return (
              <motion.text
                {...PAGE_CLOCK}
                key={s.n}
                data-arrive=""
                className={styles.spanLabel}
                x={fromRight ? x(s.to) - 6 : x(s.from) + 6}
                y={M.top + 16 + (s.n - 1) * 16}
                textAnchor={fromRight ? "end" : "start"}
                initial={false}
                animate={{ opacity: waiting ? 0 : 1 }}
                transition={timing(DUR.quick, SPANS.delay)}
              >
                Violation {s.n}: up to {s.v.maxRatePerSec}/s{wide ? " (sliding window)" : ""}
              </motion.text>
            );
          })}

          <text className={styles.axisTitle} x={M.left + plotW / 2} y={HEIGHT - 6} textAnchor="middle">
            Time (second of capture)
          </text>
          <text
            className={styles.axisTitle}
            transform={`translate(14 ${M.top + plotH / 2}) rotate(-90)`}
            textAnchor="middle"
          >
            Flashes per second
          </text>
        </svg>
      </div>

      {/* Pointer readout, one line, reserved height. aria-hidden: the data
          table below is the accessible path to the same numbers. */}
      <p className={`${styles.readout} num`} aria-hidden="true">
        {hovered
          ? `Second ${hovered.t}–${hovered.t + 1}: ${hovered.generalFlashes} general, ${hovered.redFlashes} red`
          : "Point at a second to read its counts."}
      </p>

      {/* A disclosure: aria-expanded tells the truth because the table is
          really hidden (the `hidden` attribute) while collapsed. Screen reader
          users get the same facts from the chart's text alternative. */}
      <button
        type="button"
        className={`btn btn-small ${styles.tableToggle}`}
        aria-expanded={showTable}
        aria-controls="timeline-table"
        onKeyDown={safe.onKeyDown}
        onClick={() => safe.run(() => setShowTable((v) => !v))}
      >
        {showTable ? "Hide data table" : "Show data table"}
      </button>

      <div className="table-wrap" id="timeline-table" hidden={!showTable}>
        <table>
          <caption>
            Flashes counted in each whole second. The last column says whether a violation (found by checking every
            one-second window) overlaps that second.
          </caption>
          <thead>
            <tr>
              <th scope="col">Second</th>
              <th scope="col" className="numeric">
                General flashes
              </th>
              <th scope="col" className="numeric">
                Red flashes
              </th>
              <th scope="col">Inside a violation?</th>
            </tr>
          </thead>
          <tbody>
            {bins.map((b) => {
              const inside = spans.filter((s) => s.from < b.t + 1 && s.to > b.t);
              return (
                <tr key={b.t}>
                  <th scope="row">
                    {b.t}–{b.t + 1} s
                  </th>
                  <td className="numeric">{b.generalFlashes}</td>
                  <td className="numeric">{b.redFlashes}</td>
                  <td>
                    {inside.length
                      ? `Yes: ${inside.map((s) => `violation ${s.n} (${s.v.type}, up to ${s.v.maxRatePerSec} per second)`).join(", ")}`
                      : "No"}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
