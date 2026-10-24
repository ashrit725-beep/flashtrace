// Small, pure formatting helpers shared by the screens. Keeping wording in one
// place means a phase or strategy is described the same way everywhere.

import type { AttributeMode, CaptureStrategy, JobPhase, JobSummary, Size } from "./api";

/** "24 Oct 2026, 15:30:12" in the viewer's own time zone. */
export function formatDateTime(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString("en-GB", { dateStyle: "medium", timeStyle: "medium" });
}

/** Time of frame `index` in seconds, to the millisecond: frame i is at i / fps. */
export function frameTime(index: number, fps: number): string {
  return (index / fps).toFixed(3);
}

export function percent(fraction: number): string {
  return `${Math.round(fraction * 100)}%`;
}

export function sizeLabel(size: Size): string {
  return `${size.width} × ${size.height}`;
}

// The strategy names how frames were taken, not whether that worked: a
// stepped capture can still end up non-deterministic (docs/SPEC.md 6.2), and
// the report's separate "Deterministic" field says which.
export const STRATEGY_LABEL: Record<CaptureStrategy, string> = {
  waapi_step: "Animation clock stepped",
  virtual_time: "Virtual time",
  realtime: "Real time",
  none: "No capture",
};

export const RED_RULE_LABEL: Record<string, string> = {
  wcag22: "WCAG 2.2 red flash",
  "wcag21-legacy": "WCAG 2.1 legacy red flash",
};

export const ATTRIBUTE_LABEL: Record<AttributeMode, string> = {
  none: "None",
  bbox: "Bounding boxes",
  ablate: "Ablation",
};

export const PHASE_LABEL: Record<JobPhase, string> = {
  queued: "Waiting in queue",
  capturing: "Capturing frames",
  analyzing: "Analyzing",
  attributing: "Attributing",
  ablating: "Ablating",
  verifying: "Verifying fix",
  done: "Done",
  error: "Stopped by an error",
};

/** "Capturing frames 312/600", "Analyzing", "Attributing 3/6": what the job is doing now. */
export function phaseText(job: Pick<JobSummary, "phase" | "progress">): string {
  const base = PHASE_LABEL[job.phase] ?? job.phase;
  const { current, total } = job.progress;
  const counted = job.phase !== "done" && job.phase !== "error" && total > 0;
  return counted ? `${base} ${current}/${total}` : base;
}
