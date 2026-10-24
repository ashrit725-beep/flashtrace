"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { frameUrl, type Culprit, type Rect, type Report, type Size } from "@/lib/api";
import { frameTime } from "@/lib/format";
import { RAMP_CSS, decodeHeatmap, heatmapPixels, maxCount, rampColor, rampT } from "@/lib/heatmap";
import { useSafeToggle } from "@/lib/useSafeToggle";
import styles from "./Report.module.css";

interface Props {
  jobId: string;
  report: Report;
  selected: Culprit | null;
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
function boxStyle(rect: Rect, frame: Size) {
  const left = clamp(rect.x, 0, frame.width);
  const top = clamp(rect.y, 0, frame.height);
  const right = clamp(rect.x + rect.w, 0, frame.width);
  const bottom = clamp(rect.y + rect.h, 0, frame.height);
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

export default function HeatmapView({ jobId, report, selected }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [overlay, setOverlay] = useState(true);
  // SAFETY: the overlay covers the flashing region and swaps the captured
  // frame for strong heat colours, so it must not flip faster than 3 Hz.
  const safe = useSafeToggle();
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
  }, [counts, max, heatmap.w, heatmap.h, overlay]);

  const covered = { width: heatmap.w * heatmap.cellSize, height: heatmap.h * heatmap.cellSize };

  return (
    <div>
      <figure className={styles.figure}>
        {/* The empty bands above and below the picture give a box at the very
            top or bottom of the frame room for its label. */}
        <div className={styles.stageFrame}>
          <div className={styles.stage}>
            <img
              src={frameUrl(jobId, stillIndex)}
              alt={`Captured frame ${stillIndex} (t = ${frameTime(stillIndex, settings.fps)} s)`}
              width={frame.width}
              height={frame.height}
            />
            {overlay && max > 0 && (
              <canvas
                ref={canvasRef}
                className={styles.heatCanvas}
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
            {selected && (
              <div
                className={`${styles.box} ${styles.culpritBox} ${labelSide(selected.rect, frame)}`}
                style={boxStyle(selected.rect, frame)}
                aria-hidden="true"
                data-testid="culprit-outline"
              >
                <span className={styles.boxLabel} data-testid="culprit-label">
                  Culprit {selected.rank}: {selected.selector}
                </span>
              </div>
            )}
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
        <figcaption className={styles.figcaption}>
          Still frame {stillIndex} (t = {frameTime(stillIndex, settings.fps)} s)
          {overlay && max > 0 ? " with the flash heatmap on top" : ""}.{" "}
          {worst
            ? `The dashed box marks the worst ${worstSize} px window of violation 1, at x ${worst.x}, y ${worst.y}.`
            : "No violation window to mark."}{" "}
          <span aria-live="polite">
            {selected
              ? `The solid box outlines culprit ${selected.rank} (${selected.selector}) at x ${selected.rect.x}, y ${selected.rect.y}, ${selected.rect.w} × ${selected.rect.h} px.`
              : ""}
          </span>
        </figcaption>
      </figure>

      {max > 0 && (
        <div className="btn-row" style={{ marginTop: 12 }}>
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
