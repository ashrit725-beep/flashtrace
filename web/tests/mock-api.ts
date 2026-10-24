// Fulfils the FlashTrace API (docs/API.md) inside the browser from the
// synthetic fixtures in tests/fixtures (TEST FIXTURE DATA, not measurements).
// One switch, `mode`, puts every endpoint into the state a test needs:
//   ok      - normal answers (default)
//   empty   - GET /jobs returns []
//   error   - every API call answers 500 {"detail": ...}
//   loading - every API call hangs until release() (or the page closes)

import fs from "node:fs";
import path from "node:path";
import type { Page, Route } from "@playwright/test";

type Json = Record<string, unknown>;

const FIXTURES = path.join(__dirname, "fixtures");
const readJson = <T>(file: string): T => JSON.parse(fs.readFileSync(path.join(FIXTURES, file), "utf8")) as T;
const readPng = (file: string) => fs.readFileSync(path.join(FIXTURES, file));

export const IDS = {
  FAIL: "20261024-153012-aa0001",
  PASS: "20261024-140240-aa0002",
  INCONCLUSIVE: "20261024-124503-aa0003",
  RUNNING: "20261024-161511-aa0004",
  ERROR: "20261024-163005-aa0005",
} as const;

/** The id POST /jobs hands out. */
export const NEW_JOB_ID = "20261024-170000-aa0006";

export type MockMode = "ok" | "empty" | "error" | "loading";

export interface MockOptions {
  mode?: MockMode;
  /** Extra latency on every answer, in ms. */
  delayMs?: number;
}

export interface MockApi {
  /** Every API request seen: method, path and time (ms since mocking began). */
  calls: { method: string; path: string; at: number; body?: unknown }[];
  /** Let held ("loading") requests continue. */
  release: () => void;
}

const REPORT_FILES: Record<string, string> = {
  [IDS.FAIL]: "report-fail.json",
  [IDS.PASS]: "report-pass.json",
  [IDS.INCONCLUSIVE]: "report-inconclusive.json",
};

/** The FAIL fixture is a 4 Hz square wave at 60 fps: two frame states. */
const failFrameState = (i: number) => Math.floor((i * 8) / 60) % 2;

export async function mockApi(page: Page, options: MockOptions = {}): Promise<MockApi> {
  const mode = options.mode ?? "ok";
  const started = Date.now();
  const jobs = readJson<Json[]>("jobs.json");
  const reports = new Map<string, Json>(Object.entries(REPORT_FILES).map(([id, f]) => [id, readJson<Json>(f)]));
  const frames = { a: readPng("frame-a.png"), b: readPng("frame-b.png"), still: readPng("frame-still.png") };
  const verifyPending = new Map<string, number>(); // job id -> fix index being verified
  const api: MockApi = { calls: [], release: () => undefined };
  const held = new Promise<void>((resolve) => (api.release = resolve));

  const json = (route: Route, status: number, body: unknown) =>
    route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
  const notFound = (route: Route, what: string) => json(route, 404, { detail: `${what} not found.` });

  async function gate() {
    if (mode === "loading") await held;
    if (options.delayMs) await new Promise((r) => setTimeout(r, options.delayMs));
  }

  async function handle(route: Route) {
    const request = route.request();
    const { pathname } = new URL(request.url());
    const method = request.method();
    let body: unknown;
    try {
      body = request.postDataJSON();
    } catch {
      body = undefined;
    }
    api.calls.push({ method, path: pathname, at: Date.now() - started, body });
    await gate();

    if (mode === "error") return json(route, 500, { detail: "The FlashTrace server hit an internal error." });

    if (pathname === "/health") return json(route, 200, { ok: true, version: "0.1.0", engineVersion: "1.0.0" });

    if (pathname === "/jobs" && method === "GET") return json(route, 200, mode === "empty" ? [] : jobs);
    if (pathname === "/jobs" && method === "POST") {
      const b = (body ?? {}) as Json;
      jobs.unshift({
        id: NEW_JOB_ID,
        url: b.url,
        createdAt: "2026-10-24T17:00:00Z",
        status: "queued",
        phase: "queued",
        progress: { current: 0, total: 0, label: "" },
        settings: {
          duration: b.duration ?? 10,
          viewport: b.viewport ?? { width: 1024, height: 768 },
          attribute: b.attribute ?? "bbox",
        },
        verdict: null,
        reason: null,
        error: null,
      });
      return json(route, 201, { id: NEW_JOB_ID });
    }

    const m = pathname.match(/^\/jobs\/([^/]+)(\/.*)?$/);
    if (!m) return notFound(route, "Endpoint");
    const id = decodeURIComponent(m[1]);
    const rest = m[2] ?? "";
    const job = jobs.find((j) => j.id === id);
    if (!job) return notFound(route, `Job ${id}`);

    if (rest === "" && method === "GET") {
      // A fix verification "finishes" on the first poll after it started:
      // mark the fix verified (after = PASS) and return the job as done.
      const fix = verifyPending.get(id);
      if (fix !== undefined) {
        verifyPending.delete(id);
        const report = reports.get(id) as { fixes: Json[] } | undefined;
        if (report) {
          report.fixes[fix] = {
            ...report.fixes[fix],
            verified: true,
            after: {
              verdict: "PASS",
              generalMaxRatePerSec: 0,
              redMaxRatePerSec: 0,
              qualifyingGeneral: 0,
              qualifyingRed: 0,
              deterministic: true,
            },
          };
        }
        Object.assign(job, { status: "done", phase: "done" });
      }
      return json(route, 200, job);
    }
    if (rest === "" && method === "DELETE") {
      jobs.splice(jobs.indexOf(job), 1);
      return route.fulfill({ status: 204, body: "" });
    }
    if (rest === "/report") {
      if (job.status !== "done") {
        return job.status === "error"
          ? notFound(route, "Report")
          : json(route, 409, { detail: "The job is not done yet." });
      }
      const report = reports.get(id);
      return report ? json(route, 200, report) : notFound(route, "Report");
    }
    if (rest === "/report.html") {
      return route.fulfill({ status: 200, contentType: "text/html", body: "<!doctype html><title>Report</title>" });
    }
    const frame = rest.match(/^\/frames\/(\d+)\.png$/);
    if (frame) {
      const i = Number(frame[1]);
      const report = reports.get(id) as { capture: { frameCount: number } } | undefined;
      if (!report || i >= report.capture.frameCount) return notFound(route, `Frame ${i}`);
      const png = id === IDS.FAIL ? (failFrameState(i) === 0 ? frames.a : frames.b) : frames.still;
      return route.fulfill({ status: 200, contentType: "image/png", body: png });
    }
    if (rest === "/ablate" && method === "POST") {
      Object.assign(job, { status: "running", phase: "ablating", progress: { current: 1, total: 6, label: "ablating 1/6" } });
      return json(route, 202, job);
    }
    const verify = rest.match(/^\/fixes\/(\d+)\/verify$/);
    if (verify && method === "POST") {
      verifyPending.set(id, Number(verify[1]));
      Object.assign(job, { status: "running", phase: "verifying" });
      return json(route, 202, job);
    }
    return notFound(route, "Endpoint");
  }

  await page.route("**/health", handle);
  await page.route("**/jobs**", handle);
  return api;
}

// ------------------------------------------------------- per-test overrides
// Playwright tries routes newest first, so these, registered after mockApi,
// see a request before the mock does; route.fallback() hands it on.

/** A fixture file, parsed (a fresh copy every call, safe to modify). */
export const readFixture = <T = Json>(file: string): T => readJson<T>(file);

const matches = (path: RegExp) => (url: URL) => path.test(url.pathname);

export interface FailOptions {
  /** Let this many matching calls through to the normal mock first. */
  skip?: number;
  /** Never answer: the request stays pending (e.g. to hold a "Verifying…" state). */
  hang?: boolean;
  detail?: string;
}

/** Make `method` requests whose path matches `path` fail with `status` (or hang). */
export async function failApi(page: Page, method: string, path: RegExp, status = 500, options: FailOptions = {}) {
  let seen = 0;
  await page.route(matches(path), async (route) => {
    if (route.request().method() !== method) return route.fallback();
    seen += 1;
    if (seen <= (options.skip ?? 0)) return route.fallback();
    if (options.hang) return; // not fulfilled: the browser keeps waiting
    return route.fulfill({
      status,
      contentType: "application/json",
      body: JSON.stringify({ detail: options.detail ?? `Test failure ${status}.` }),
    });
  });
}

/**
 * Answer GET requests whose path matches `path` with answer(n) (n = 1 for the
 * first matching call); when answer returns undefined the normal mock answers.
 */
export async function replaceJson(page: Page, path: RegExp, answer: (n: number) => unknown) {
  let seen = 0;
  await page.route(matches(path), async (route) => {
    if (route.request().method() !== "GET") return route.fallback();
    seen += 1;
    const body = answer(seen);
    if (body === undefined) return route.fallback();
    return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  });
}

/** Every API request the page makes (including ones an override answered), in order. */
export function recordRequests(page: Page) {
  const seen: { method: string; path: string; body: string | null }[] = [];
  page.on("request", (request) => {
    const { pathname } = new URL(request.url());
    if (pathname.startsWith("/jobs") || pathname === "/health") {
      seen.push({ method: request.method(), path: pathname, body: request.postData() });
    }
  });
  return seen;
}
