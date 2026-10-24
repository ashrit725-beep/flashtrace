// Typed client for the local FlashTrace server (contract: docs/API.md).
// The report types mirror engine/schema/report.schema.json by hand, so a
// schema change must be copied here too; the Playwright fixtures are
// schema-validated and will catch drift in the fields the UI renders.

/** Same origin by default: `flashtrace serve` hosts this UI and the API together. */
export const API_BASE: string = process.env.NEXT_PUBLIC_API_BASE ?? "";

// ---------------------------------------------------------------- job types

export type JobStatus = "queued" | "running" | "done" | "error" | "cancelled";
export type JobPhase =
  | "queued"
  | "capturing"
  | "analyzing"
  | "attributing"
  | "ablating"
  | "verifying"
  | "done"
  | "error";
export type AttributeMode = "none" | "bbox" | "ablate";
export type Verdict = "PASS" | "FAIL" | "INCONCLUSIVE";

export interface Size {
  width: number;
  height: number;
}

export interface Progress {
  current: number;
  total: number;
  label: string;
}

export interface JobSettings {
  duration: number;
  viewport: Size;
  attribute: AttributeMode;
}

export interface JobSummary {
  id: string;
  url: string;
  createdAt: string;
  status: JobStatus;
  phase: JobPhase;
  progress: Progress;
  settings: JobSettings;
  verdict: Verdict | null;
  reason: string | null;
  error: string | null;
}

export interface NewJob {
  url: string;
  duration?: number;
  viewport?: Size;
  attribute?: AttributeMode;
}

export interface Health {
  ok: boolean;
  version: string;
  engineVersion: string;
}

// ------------------------------------------------------------- report types

export type FlashType = "general" | "red";

export interface Rect {
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface Measurement {
  verdict: Verdict;
  generalMaxRatePerSec: number;
  redMaxRatePerSec: number;
  qualifyingGeneral: number;
  qualifyingRed: number;
  deterministic: boolean;
}

export interface Violation {
  start: number;
  end: number;
  type: FlashType;
  maxRatePerSec: number;
  window: Rect;
  frameIndices: number[];
}

export interface Advisory {
  type: "sustained-flashing";
  flashType: FlashType;
  start: number;
  end: number;
  meanRatePerSec: number;
  label: "advisory - not a WCAG 2.3.1 criterion";
}

export interface TimelineBin {
  t: number;
  generalFlashes: number;
  redFlashes: number;
}

export interface Heatmap {
  cellSize: number;
  w: number;
  h: number;
  /** base64 of a little-endian uint16 array, row-major, h rows by w columns */
  data: string;
}

export interface CulpritAnimation {
  name: string;
  /** milliseconds; null when unknown or infinite */
  duration: number | null;
  keyframes: Record<string, unknown>[];
}

export interface Culprit {
  rank: number;
  selector: string;
  nodeType: string;
  ftId: string | null;
  rect: Rect;
  animation: CulpritAnimation | null;
  overlap: number;
  /** null when ablation was not run */
  ablationDelta: number | null;
  inMinimalSet: boolean;
}

export type FixKind = "rate" | "luminance" | "red" | "area";

export interface Fix {
  /** rank of the culprit this fix targets */
  culprit: number;
  kind: FixKind;
  css: string;
  rationale: string;
  before: Measurement;
  after: Measurement | null;
  verified: boolean;
}

export interface EvidenceFrame {
  index: number;
  pngPath: string;
  label: string;
}

export type CaptureStrategy = "waapi_step" | "virtual_time" | "realtime" | "none";

export interface Report {
  version: string;
  url: string;
  capturedAt: string;
  engineVersion: string;
  settings: {
    duration: number;
    viewport: Size;
    fps: number;
    redRule: "wcag22" | "wcag21-legacy";
    attribute: AttributeMode;
    windowPx: Size;
    cellPx: number;
  };
  capture: {
    strategy: CaptureStrategy;
    deterministic: boolean;
    frameCount: number;
    hash: string;
    frameHashes: string[];
    fallbackReason: string | null;
    uncontrollable: string[];
    notes: string[];
  };
  verdict: Verdict;
  reason: string;
  violations: Violation[];
  advisory: Advisory[];
  timeline: TimelineBin[];
  heatmap: Heatmap;
  culprits: Culprit[];
  fixes: Fix[];
  evidenceFrames: EvidenceFrame[];
  limitations: string[];
  assumptions: string[];
  disclaimer: "Analysis aid, not a compliance certification.";
}

// ------------------------------------------------------------------ errors

/** Every failed call throws this, so screens can show `detail` and branch on `status`. */
export class ApiError extends Error {
  readonly status: number;
  readonly detail: string;

  constructor(status: number, detail: string) {
    super(detail);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

/** status 0 = the request never reached a server (it is not running, or the network failed). */
export const NETWORK_ERROR_STATUS = 0;

// FastAPI returns {"detail": "sentence"} for our own errors but a list of
// {msg, loc} objects for request-validation 422s; turn both into one sentence.
function detailFrom(body: unknown, status: number): string {
  const fallback = `The server answered with status ${status}.`;
  if (!body || typeof body !== "object" || !("detail" in body)) return fallback;
  const detail = (body as { detail: unknown }).detail;
  if (typeof detail === "string" && detail.trim()) return detail;
  if (Array.isArray(detail)) {
    const messages = detail
      .map((d) => (d && typeof d === "object" && "msg" in d ? String((d as { msg: unknown }).msg) : ""))
      .filter(Boolean);
    if (messages.length) return messages.join("; ");
  }
  return fallback;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(API_BASE + path, {
      ...init,
      headers: { Accept: "application/json", ...(init?.body ? { "Content-Type": "application/json" } : {}) },
      // Job state changes every second; a cached answer would freeze the UI.
      cache: "no-store",
    });
  } catch {
    throw new ApiError(NETWORK_ERROR_STATUS, "Could not reach the FlashTrace server.");
  }
  if (!response.ok) {
    let body: unknown = null;
    try {
      body = await response.json();
    } catch {
      // Not JSON (for example a proxy error page): fall back to the status line.
    }
    throw new ApiError(response.status, detailFrom(body, response.status));
  }
  if (response.status === 204) return undefined as T;
  try {
    return (await response.json()) as T;
  } catch {
    throw new ApiError(response.status, "The server sent a response that is not valid JSON.");
  }
}

// --------------------------------------------------------------- endpoints

const job = (id: string) => `/jobs/${encodeURIComponent(id)}`;

export const getHealth = () => request<Health>("/health");

export const createJob = (body: NewJob) =>
  request<{ id: string }>("/jobs", { method: "POST", body: JSON.stringify(body) });

export const listJobs = () => request<JobSummary[]>("/jobs");

export const getJob = (id: string) => request<JobSummary>(job(id));

export const getReport = (id: string) => request<Report>(`${job(id)}/report`);

export const ablate = (id: string) => request<JobSummary>(`${job(id)}/ablate`, { method: "POST" });

export const verifyFix = (id: string, n: number) =>
  request<JobSummary>(`${job(id)}/fixes/${n}/verify`, { method: "POST" });

export const deleteJob = (id: string) => request<void>(job(id), { method: "DELETE" });

/** Captured frame i (0-based) as a PNG; any index 0 … frameCount-1. */
export const frameUrl = (id: string, i: number) => `${API_BASE}${job(id)}/frames/${i}.png`;

export const reportHtmlUrl = (id: string) => `${API_BASE}${job(id)}/report.html`;

export const reportJsonUrl = (id: string) => `${API_BASE}${job(id)}/report`;

/**
 * The report can be read now: the job is done AND no re-run is in progress.
 * Both fields are checked because docs/API.md only says that ablate/verify
 * move the job to phase `ablating`/`verifying`; it does not promise that
 * `status` leaves "done" meanwhile. Trusting `status` alone would treat a job
 * that is still re-running as finished.
 */
export const isReportReady = (job: Pick<JobSummary, "status" | "phase">) =>
  job.status === "done" && job.phase === "done";

/** Nothing more will change (done, failed or cancelled), so polling can stop. */
export const isFinished = (job: Pick<JobSummary, "status" | "phase">) =>
  job.status === "error" || job.status === "cancelled" || isReportReady(job);
