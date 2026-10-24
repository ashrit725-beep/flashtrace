// Heatmap helpers. report.heatmap.data is base64 of a little-endian uint16
// array, row-major, h rows by w columns (engine/schema/report.schema.json):
// each value is how many flashes completed in that cell over the capture.

import type { Heatmap } from "./api";

/** Decode the base64 cell counts. DataView reads little-endian explicitly,
 *  so the result is right on any machine's byte order. */
export function decodeHeatmap(heatmap: Heatmap): Uint16Array {
  const cells = heatmap.w * heatmap.h;
  const out = new Uint16Array(cells);
  if (!heatmap.data || cells === 0) return out;
  const binary = atob(heatmap.data);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  const view = new DataView(bytes.buffer);
  const n = Math.min(cells, Math.floor(bytes.length / 2));
  for (let i = 0; i < n; i++) out[i] = view.getUint16(i * 2, true);
  return out;
}

/** Sequential ramp, light yellow -> deep red (ColorBrewer YlOrRd stops). One
 *  hue direction, so "more" always reads as "darker and redder". */
export const RAMP: [number, number, number][] = [
  [255, 237, 160],
  [254, 178, 76],
  [240, 59, 32],
  [128, 0, 38],
];

/** CSS gradient for the legend, built from the same stops. */
export const RAMP_CSS = `linear-gradient(to right, ${RAMP.map(([r, g, b]) => `rgb(${r} ${g} ${b})`).join(", ")})`;

/** Colour for t in [0, 1] by linear interpolation between the ramp stops. */
export function rampColor(t: number): [number, number, number] {
  const x = Math.min(1, Math.max(0, t)) * (RAMP.length - 1);
  const i = Math.min(RAMP.length - 2, Math.floor(x));
  const f = x - i;
  const [a, b] = [RAMP[i], RAMP[i + 1]];
  return [0, 1, 2].map((k) => Math.round(a[k] + (b[k] - a[k]) * f)) as [number, number, number];
}

/**
 * Where a count sits on the ramp, 0 to 1. Zero cells are never painted, so
 * the ramp starts at 1 flash (t = 0, light yellow) and ends at the busiest
 * cell (t = 1, deep red). The legend places its tick labels with this same
 * function, so a label always sits over the colour its count is painted in.
 * With max 1 every painted cell is the busiest one, so t = 1.
 */
export function rampT(count: number, max: number): number {
  return max > 1 ? (count - 1) / (max - 1) : 1;
}

/** Paint the counts into an RGBA buffer: zero cells stay transparent so the
 *  frame shows through; busier cells are more opaque (0.35 to 0.85). */
export function heatmapPixels(counts: Uint16Array, max: number): Uint8ClampedArray<ArrayBuffer> {
  const rgba = new Uint8ClampedArray(new ArrayBuffer(counts.length * 4));
  if (max <= 0) return rgba;
  for (let i = 0; i < counts.length; i++) {
    const v = counts[i];
    if (v === 0) continue;
    const t = rampT(v, max);
    const [r, g, b] = rampColor(t);
    rgba[i * 4] = r;
    rgba[i * 4 + 1] = g;
    rgba[i * 4 + 2] = b;
    rgba[i * 4 + 3] = Math.round((0.35 + 0.5 * t) * 255);
  }
  return rgba;
}

export function maxCount(counts: Uint16Array): number {
  let max = 0;
  for (let i = 0; i < counts.length; i++) if (counts[i] > max) max = counts[i];
  return max;
}
