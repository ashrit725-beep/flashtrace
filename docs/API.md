# FlashTrace local server API (contract for `server/` and `web/`)

`flashtrace serve` starts one FastAPI process on `127.0.0.1:8001` (loopback only). It serves the JSON API below
and the statically exported web UI (`web/out`) from the same origin, so there is no second server, no CORS and no
Node.js at run time. Analysis aid, not a compliance certification.

Rules (from the build brief): fresh browser context per job, no stored credentials, **one job runs at a time**
(others wait in a FIFO queue), only URLs the user submitted are fetched, `file://` and local paths are allowed only
inside the project directory unless the server was started with `--allow-local-files`.

All responses are JSON unless stated. Errors use FastAPI's shape `{"detail": "<one sentence>"}` with status 400
(bad state), 404 (unknown job), 409 (job busy), 422 (invalid input).

## Types

```ts
type JobStatus = "queued" | "running" | "done" | "error" | "cancelled";
type JobPhase = "queued" | "capturing" | "analyzing" | "attributing" | "ablating" | "verifying" | "done" | "error";

interface Progress { current: number; total: number; label: string }   // e.g. {current: 312, total: 600, label: "frames 312/600"}

interface JobSummary {
  id: string;                 // e.g. "20261024-153012-a1b2c3"
  url: string;
  createdAt: string;          // ISO-8601 UTC
  status: JobStatus;
  phase: JobPhase;
  progress: Progress;
  settings: { duration: number; viewport: { width: number; height: number }; attribute: "none" | "bbox" | "ablate" };
  verdict: "PASS" | "FAIL" | "INCONCLUSIVE" | null;   // null until done
  reason: string | null;
  error: string | null;       // one sentence when status == "error"
}
```

The report itself follows `engine/schema/report.schema.json`.

## Endpoints

| Method | Path | Body | Response |
|---|---|---|---|
| GET | `/health` | — | `{ "ok": true, "version": "0.1.0", "engineVersion": "1.0.0" }` |
| POST | `/jobs` | `{ "url": string, "duration"?: number (1–60, default 10), "viewport"?: {width,height} (default 1024×768; each side 200–3840), "attribute"?: "none"\|"bbox"\|"ablate" (default "bbox") }` | `201 { "id": string }` |
| GET | `/jobs` | — | `JobSummary[]`, newest first |
| GET | `/jobs/{id}` | — | `JobSummary` (the brief's `{status, phase, progress}` plus the fields above) |
| GET | `/jobs/{id}/report` | — | the report JSON; `409` while the job is not done; `404` if it errored before a report existed |
| GET | `/jobs/{id}/report.html` | — | the self-contained HTML export (`text/html`) |
| GET | `/jobs/{id}/frames/{i}.png` | — | captured frame `i` as `image/png` (any frame 0 … frameCount−1, used for manual prev/next scrubbing); `404` outside the range |
| POST | `/jobs/{id}/ablate` | — | `202 JobSummary`; re-runs attribution in ablation mode on a done job (phase `ablating`), then the report's `culprits` and `fixes` are replaced |
| POST | `/jobs/{id}/fixes/{n}/verify` | — | `202 JobSummary`; verifies `report.fixes[n]` (0-based) by re-capturing with the fix injected (phase `verifying`); afterwards `fixes[n].after` and `fixes[n].verified` are set |
| DELETE | `/jobs/{id}` | — | `204`; cancels the job if queued or running (its browser is closed) and deletes its directory |

Validation (422): `url` must be `http(s)://…`, `file://…` or an existing local path; local targets must lie inside
the project directory unless `--allow-local-files`; numbers in range; unknown fields rejected.

## UI routes (static export; IDs travel in the query string because a static export has no server routes)

| Route | Screen |
|---|---|
| `/` | New audit |
| `/run?id=<id>` | Running (polls `GET /jobs/{id}` every 1 s; never faster) |
| `/report?id=<id>` | Report |
| `/history` | Past jobs |

## Polling and safety

The UI polls at most once per second, never auto-plays frames, never animates anything faster than 3 Hz, honours
`prefers-reduced-motion`, and shows the disclaimer in a persistent footer on every screen.
