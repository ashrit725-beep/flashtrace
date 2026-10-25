"use client";

import { AnimatePresence, motion, useIsPresent } from "motion/react";
import { useEffect, useMemo, useRef, useState } from "react";
import { frameUrl, type Culprit, type Rect, type Report, type Size } from "@/lib/api";
import { frameTime } from "@/lib/format";
import { RAMP_CSS, decodeHeatmap, heatmapPixels, maxCount, rampColor, rampT } from "@/lib/heatmap";
import { DUR, PAGE_CLOCK, useGlideRoom, useLargeAreaGate, useMotionTokens } from "@/lib/motion";
import styles from "./Report.module.css";

interface Props {
  jobId: string;
  report: Report;
  selected: Culprit | null;
  /** True when the selection was changed from the keyboard: the outline jumps instead of gliding. */
  instant?: boolean;
}

/** The still to draw on: the first violation's first flash frame, else frame 0. */
export function worstFrameIndex(report: Report): number {
  const v = report.violations[0];
  if (!v) return 0;
  return v.frameIndices.length ? v.frameIndices[0] : v.start;
}

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));
const pct = (fraction: number) => `${fraction * 100}%`;

/**
 * A rectangle in page px -> CSS percentages of the frame, so the boxes stay
 * aligned at any display size without measuring anything. The rectangle is
 * clamped to the frame first: an element can be larger than the viewport
 * (body, a full-page overlay), and its outline should then run along the
 * picture's edge instead of off it.
 */
function clampRect(rect: Rect, frame: Size) {
  const left = clamp(rect.x, 0, frame.width);
  const top = clamp(rect.y, 0, frame.height);
  const right = clamp(rect.x + rect.w, 0, frame.width);
  const bottom = clamp(rect.y + rect.h, 0, frame.height);
  return { left, top, right, bottom };
}

function boxStyle(rect: Rect, frame: Size) {
  const { left, top, right, bottom } = clampRect(rect, frame);
  return {
    left: pct(left / frame.width),
    top: pct(top / frame.height),
    width: pct((right - left) / frame.width),
    height: pct((bottom - top) / frame.height),
  };
}

/** Boxes in the right half hang their label from their right edge, so the
 *  label runs into the picture instead of past its right side. */
function labelSide(rect: Rect, frame: Size): string {
  return rect.x + rect.w / 2 > frame.width / 2 ? styles.labelRight : "";
}

/** Tick labels for the colour ramp, each placed where its count is painted. */
function Legend({ max, cellSize }: { max: number; cellSize: number }) {
  const intro = (
    <p className="hint">
      Completed flashes per {cellSize} × {cellSize} px cell over the whole capture. Cells with no flashes are
      transparent.
    </p>
  );
  if (max === 1) {
    // One count only: a ramp would claim colours that never appear.
    const [r, g, b] = rampColor(1);
    return (
      <div className={styles.legendBox}>
        {intro}
        <p className={styles.singleSwatch}>
          <span className={styles.swatchCell} style={{ background: `rgb(${r} ${g} ${b})` }} aria-hidden="true" />
          Every flashing cell: 1 flash (deep red)
        </p>
      </div>
    );
  }
  const mid = 1 + Math.round((max - 1) / 2);
  const ticks = max <= 2 ? [1, max] : [1, mid, max];
  return (
    <div className={styles.legendBox}>
      {intro}
      <div className={styles.rampBar} style={{ background: RAMP_CSS }} aria-hidden="true" />
      <div className={styles.rampScale} aria-hidden="true">
        {ticks.map((v) => (
          <span key={v} style={{ left: pct(rampT(v, max)) }}>
            {v}
          </span>
        ))}
      </div>
      <p className="visually-hidden">
        Colour scale from 1 flash (light yellow, more transparent) to {max} flashes (deep red, more opaque).
      </p>
    </div>
  );
}

/**
 * The selected culprit's outline (web/MOTION.md 3.3). One element, kept
 * mounted while any culprit is selected and never re-keyed per culprit:
 * choosing another rank glides the same outline from the old box to the new
 * one. Fixed colours: they sit on captured frames.
 *
 * Changed from the spec's layout/layoutId glide, for SAFETY proof: the
 * outline is an SVG rect whose x, y, width and height are animated, with a
 * non-scaling stroke, and the label moves by left/top. Motion's layout
 * projection scaled the box (and counter-scaled its label) with a transform,
 * and Chromium re-rasterises a scaled layer on its own real-time schedule, so
 * two captures of the same glide differed by a pixel at scattered frames
 * (self-audit F3). Nothing here is scaled now; the stroke stays 3 px with a
 * white halo either side, and the enter is a plain fade. A leaving outline
 * drops its test ids at once, so tests and the live caption never see two.
 *
 * SAFETY: the glide runs only on screens 1024 px wide or more (useGlideRoom).
 * On a phone the flash window is 128 x 96 px, and the label (a dark block of
 * text) plus the strokes sweeping across the light frame completed flashes on
 * three frames per glide: F3 at 390 px measured 6 a second. There the outline
 * moves to the new box at once, one change per selection.
 */
const OUTLINE = { stroke: "#1d5a3c", width: 3, halo: "#ffffff", haloWidth: 7 } as const;

function CulpritOutline({ culprit, frame, instant }: { culprit: Culprit; frame: Size; instant: boolean }) {
  const m = useMotionTokens();
  const present = useIsPresent();
  const { left, top, right, bottom } = clampRect(culprit.rect, frame);
  const fromRight = labelSide(culprit.rect, frame) !== "";
  const room = useGlideRoom();
  const glide = m.glide(instant || !room);
  const box = { attrX: left, attrY: top, width: right - left, height: bottom - top };
  const rect = (stroke: string, width: number) => (
    <motion.rect
      initial={false}
      animate={box}
      transition={glide}
      fill="none"
      stroke={stroke}
      strokeWidth={width}
      vectorEffect="non-scaling-stroke"
    />
  );
  return (
    <motion.div
      {...PAGE_CLOCK}
      className={styles.outlineLayer}
      aria-hidden="true"
      data-testid={present ? "culprit-outline" : undefined}
      initial={m.from({ opacity: 0 })}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0, transition: m.tween(DUR.press) }}
      transition={m.tween(DUR.quick)}
    >
      <svg
        className={styles.outlineSvg}
        viewBox={`0 0 ${frame.width} ${frame.height}`}
        preserveAspectRatio="none"
        aria-hidden="true"
        focusable="false"
      >
        {rect(OUTLINE.halo, OUTLINE.haloWidth)}
        {rect(OUTLINE.stroke, OUTLINE.width)}
      </svg>
      <motion.span
        className={`${styles.boxLabel} ${styles.culpritLabel} ${fromRight ? styles.culpritLabelRight : ""}`}
        data-testid={present ? "culprit-label" : undefined}
        initial={false}
        animate={{ left: pct((fromRight ? right : left) / frame.width), top: pct(bottom / frame.height) }}
        transition={glide}
      >
        Culprit {culprit.rank}: {culprit.selector}
      </motion.span>
    </motion.div>
  );
}

export default function HeatmapView({ jobId, report, selected, instant = false }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [overlay, setOverlay] = useState(true);
  // SAFETY: the overlay covers the flashing region and swaps the captured
  // frame for strong heat colours, so it must not flip faster than 3 Hz. The
  // data-table disclosure moves these same cells, so both share the report's
  // one large-area gate. The canvas stays mounted and fades (160 ms), and the
  // stage's data-overlay attribute lets tests count the changes.
  const safe = useLargeAreaGate();
  const { heatmap, settings, violations } = report;
  const frame = settings.viewport;
  const stillIndex = worstFrameIndex(report);
  const worst = violations[0]?.window ?? null;
  // Label the box with its own size (it can differ from settings.windowPx by
  // the 4 px cell rounding), so the label matches the box and the Violations list.
  const worstSize = worst ? `${worst.w} × ${worst.h}` : "";

  // Decoding ~50k cells is cheap but not free: do it once per report.
  const counts = useMemo(() => decodeHeatmap(heatmap), [heatmap]);
  const max = useMemo(() => maxCount(counts), [counts]);

  // Paint one canvas pixel per heatmap cell; CSS scales it up with
  // image-rendering: pixelated so cells stay crisp squares.
  useEffect(() => {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext("2d");
    if (!canvas || !ctx || heatmap.w === 0 || heatmap.h === 0) return;
    ctx.putImageData(new ImageData(heatmapPixels(counts, max), heatmap.w, heatmap.h), 0, 0);
  }, [counts, max, heatmap.w, heatmap.h]);

  const covered = { width: heatmap.w * heatmap.cellSize, height: heatmap.h * heatmap.cellSize };

  return (
    <div>
      <figure className={styles.figure}>
        {/* The empty bands above and below the picture give a box at the very
            top or bottom of the frame room for its label. */}
        <div className={styles.stageFrame}>
          <div className={styles.stage} data-overlay={overlay && max > 0 ? "on" : "off"}>
            <img
              src={frameUrl(jobId, stillIndex)}
              alt={`Captured frame ${stillIndex} (t = ${frameTime(stillIndex, settings.fps)} s)`}
              width={frame.width}
              height={frame.height}
              // Decoded with the paint, so the still never shows blank under the overlay (MOTION.md 3.6).
              decoding="sync"
            />
            {max > 0 && (
              <canvas
                ref={canvasRef}
                className={`${styles.heatCanvas} ${overlay ? "" : styles.heatHidden}`}
                width={heatmap.w}
                height={heatmap.h}
                style={{
                  width: pct(covered.width / frame.width),
                  height: pct(covered.height / frame.height),
                }}
                aria-hidden="true"
                data-testid="heatmap-canvas"
              />
            )}
            <AnimatePresence>
              {selected && <CulpritOutline key="outline" culprit={selected} frame={frame} instant={instant} />}
            </AnimatePresence>
            {/* Drawn last so the dashed window stays visible on top of a culprit outline. */}
            {worst && (
              <div
                className={`${styles.box} ${styles.windowBox} ${labelSide(worst, frame)}`}
                style={boxStyle(worst, frame)}
                aria-hidden="true"
              >
                <span className={styles.boxLabel} data-testid="window-label">
                  Worst {worstSize} px window
                </span>
              </div>
            )}
          </div>
        </div>
        {/* SAFETY and layout: the visible caption never changes with the controls
            below it. It used to gain a sentence on every culprit selection (and a
            phrase with the overlay), which re-wrapped it at 390 px and moved the
            overlay button, the legend and the culprits table (including the
            Outline button just pressed) by about 21 px: a large-area shift on
            its own gate (self-audit F3 at 390 px: 8 flashes a second). What the
            outline marks is announced in a visually hidden polite live region,
            and the outline's own label names the culprit on the picture. */}
        <figcaption className={styles.figcaption}>
          Still frame {stillIndex} (t = {frameTime(stillIndex, settings.fps)} s)
          {max > 0 ? ", with the flash heatmap as an overlay you can turn off" : ""}.{" "}
          {worst
            ? `The dashed box marks the worst ${worstSize} px window of violation 1, at x ${worst.x}, y ${worst.y}.`
            : "No violation window to mark."}
          {report.culprits.length > 0 ? " Select a culprit below to outline it with a solid box." : ""}
          <span className="visually-hidden" aria-live="polite">
            {selected
              ? `The solid box outlines culprit ${selected.rank} (${selected.selector}) at x ${selected.rect.x}, y ${selected.rect.y}, ${selected.rect.w} × ${selected.rect.h} px.`
              : ""}
          </span>
        </figcaption>
      </figure>

      {max > 0 && (
        <div className={`btn-row ${styles.overlayRow}`}>
          <button
            type="button"
            className="btn btn-small"
            aria-pressed={overlay}
            onKeyDown={safe.onKeyDown}
            onClick={() => safe.run(() => setOverlay((v) => !v))}
          >
            {/* State is carried by aria-pressed; the ": on/off" suffix is visual only. */}
            <span>
              Heatmap overlay<span aria-hidden="true">{overlay ? ": on" : ": off"}</span>
            </span>
          </button>
        </div>
      )}

      {max > 0 ? (
        <Legend max={max} cellSize={heatmap.cellSize} />
      ) : (
        <p className="muted">No flashes were recorded anywhere on the page, so the heatmap is empty.</p>
      )}
    </div>
  );
}
