"""A stand-in FlashTrace server for the UI self-audit (web/MOTION.md section 7, ADR-024).

FlashTrace audits its own web UI: ``tests/test_ui_self_audit.py`` captures the built UI with
``capture.capture`` and analyses it with ``engine.analyze``. The capture runs in a real browser on
the capture's own clock, so the API cannot be mocked inside the page (``web/tests/mock-api.ts`` uses
Playwright routes). This module serves the same answers over HTTP instead. Standard library only.

* Static files come from ``web/out`` (the ``next build`` export). A directory serves its
  ``index.html``; the query string (``?id=``, ``?_rsc=``) is ignored, so client navigation loads its
  ``.txt`` payloads; an unknown path serves ``404.html`` with status 404.
* The docs/API.md endpoints answer from ``web/tests/fixtures`` (TEST FIXTURE DATA, not measurements),
  with the ids and semantics of ``web/tests/mock-api.ts``.
* ``GET /jobs/<RUNNING>`` advances once per request (312 -> 432 -> 552 frames, analysing, attributing,
  done), so the Running screen's hand-off to the report is deterministic.
* Every response carries ``Cache-Control: no-store``. State lives in each server instance.

``/__control/strobe.html`` is the self-audit's control: a full-viewport black and white swap at 5 Hz
that the pipeline must FAIL. It is never linked from the UI and never shown on a screen: it swaps
only when ``navigator.webdriver`` is true and the URL has ``?run=1``; otherwise it shows a static
sentence (in the spirit of docs/SPEC.md 9.2).

Run by hand: ``python tests/ui_mock_server.py --port 4318`` (after ``npm run build`` in web/).
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import threading
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "web" / "out"
DEFAULT_FIXTURES = ROOT / "web" / "tests" / "fixtures"

IDS = {
    "FAIL": "20261024-153012-aa0001",
    "PASS": "20261024-140240-aa0002",
    "INCONCLUSIVE": "20261024-124503-aa0003",
    "RUNNING": "20261024-161511-aa0004",
    "ERROR": "20261024-163005-aa0005",
}
NEW_JOB_ID = "20261024-170000-aa0006"
REPORT_FILES = {IDS["FAIL"]: "report-fail.json", IDS["PASS"]: "report-pass.json", IDS["INCONCLUSIVE"]: "report-inconclusive.json"}

TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json",
    ".txt": "text/plain; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
}

#: What GET /jobs/<RUNNING> answers on its n-th request (1-based); later requests answer "done".
#: Mirrors server/runner.py: all frames in means "analyzing" (600/600); attribution has no count.
RUNNING_STEPS: list[tuple[str, int, int, str]] = [
    ("capturing", 312, 600, "frames 312/600"),
    ("capturing", 432, 600, "frames 432/600"),
    ("capturing", 552, 600, "frames 552/600"),
    ("analyzing", 600, 600, "analysing frames"),
    ("attributing", 0, 0, "matching elements"),
]

STROBE_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Self-audit control</title>
<style>
  html, body { margin: 0; height: 100%; background: #fff; color: #111; font: 16px/1.5 system-ui, sans-serif; }
  p { margin: 24px; max-width: 40em; }
  #stage { position: fixed; inset: 0; background: #fff; }
</style>
</head>
<body>
<p id="note">This is a control page for FlashTrace's automated self-audit. It shows nothing here:
its test pattern only runs inside the headless audit browser.</p>
<div id="stage" hidden></div>
<script>
(() => {
  // Headless audit only: never swap on a screen someone is looking at.
  const run = navigator.webdriver === true && new URLSearchParams(location.search).get("run") === "1";
  if (!run) return;
  document.getElementById("note").hidden = true;
  const stage = document.getElementById("stage");
  stage.hidden = false;
  let dark = false;
  // 5 Hz square wave: a swap every 100 ms on the page clock (the capture's fake clock).
  const swap = () => { dark = !dark; stage.style.background = dark ? "#000" : "#fff"; setTimeout(swap, 100); };
  setTimeout(swap, 100);
})();
</script>
</body>
</html>
"""


def _read_json(fixtures: Path, name: str) -> Any:
    return json.loads((fixtures / name).read_text(encoding="utf-8"))


class MockState:
    """Everything one server instance remembers: the job list, reports and request counters."""

    def __init__(self, fixtures: Path, frames: str = "square", jobs: int | None = None) -> None:
        if frames not in ("square", "alternate"):
            raise ValueError("frames must be 'square' or 'alternate'")
        self.lock = threading.Lock()
        self.frames_mode = frames
        self.pngs = {name: (fixtures / f"frame-{name}.png").read_bytes() for name in ("a", "b", "still")}
        self.reports: dict[str, dict[str, Any]] = {jid: _read_json(fixtures, f) for jid, f in REPORT_FILES.items()}
        fixture_jobs: list[dict[str, Any]] = _read_json(fixtures, "jobs.json")
        self.jobs = fixture_jobs if jobs is None else self._finished_jobs(fixture_jobs, jobs)
        self.running_requests = 0
        self.verify_pending: dict[str, int] = {}

    def _finished_jobs(self, fixture_jobs: list[dict[str, Any]], count: int) -> list[dict[str, Any]]:
        """``count`` finished jobs, newest first: the FAIL fixture, then copies cycling FAIL/PASS/INCONCLUSIVE."""
        templates = [next(j for j in fixture_jobs if j["id"] == IDS[k]) for k in ("FAIL", "PASS", "INCONCLUSIVE")]
        newest = datetime(2026, 10, 24, 23, 0, tzinfo=timezone.utc)
        out: list[dict[str, Any]] = []
        for k in range(count):
            template = templates[k % 3]
            job = copy.deepcopy(template)
            if k > 0:
                job["id"] = f"20261024-{230000 - k * 100:06d}-bb{k:04d}"
                job["url"] = f"{template['url']}page-{k}/"
                self.reports[job["id"]] = copy.deepcopy(self.reports[template["id"]])
            job["createdAt"] = (newest - timedelta(minutes=7 * k)).strftime("%Y-%m-%dT%H:%M:%SZ")
            out.append(job)
        return out

    def job(self, job_id: str) -> dict[str, Any] | None:
        return next((j for j in self.jobs if j["id"] == job_id), None)

    def advance_running(self, job: dict[str, Any]) -> None:
        """The RUNNING fixture moves one step per GET, then is done and has the FAIL report."""
        self.running_requests += 1
        n = self.running_requests
        if n <= len(RUNNING_STEPS):
            phase, current, total, label = RUNNING_STEPS[n - 1]
            job.update(status="running", phase=phase, progress={"current": current, "total": total, "label": label})
            return
        if job["status"] != "done":
            fail = self.reports[IDS["FAIL"]]
            job.update(status="done", phase="done", progress={"current": 1, "total": 1, "label": "done"},
                       verdict=fail["verdict"], reason=fail["reason"])
            self.reports[job["id"]] = copy.deepcopy(fail)

    def finish_verify(self, job: dict[str, Any]) -> None:
        """A fix verification finishes on the first poll after it started (as mock-api.ts)."""
        index = self.verify_pending.pop(job["id"], None)
        if index is None:
            return
        report = self.reports.get(job["id"])
        if report is not None and 0 <= index < len(report["fixes"]):
            report["fixes"][index] = {
                **report["fixes"][index],
                "verified": True,
                "after": {"verdict": "PASS", "generalMaxRatePerSec": 0, "redMaxRatePerSec": 0,
                          "qualifyingGeneral": 0, "qualifyingRed": 0, "deterministic": True},
            }
        job.update(status="done", phase="done")

    def frame_png(self, job_id: str, index: int) -> bytes:
        if job_id != IDS["FAIL"]:
            return self.pngs["still"]
        # FAIL is a 4 Hz square wave at 60 fps; "alternate" swaps on every frame (every step is a full swap).
        state = index % 2 if self.frames_mode == "alternate" else (index * 8 // 60) % 2
        return self.pngs["a" if state == 0 else "b"]


def _make_handler(out_dir: Path, state: MockState) -> type[BaseHTTPRequestHandler]:
    out_root = out_dir.resolve()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - quiet test output
            return

        # ------------------------------------------------------------ responses
        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, status: int, payload: Any) -> None:
            self._send(status, json.dumps(payload).encode("utf-8"), "application/json")

        def _not_found(self, what: str) -> None:
            self._json(404, {"detail": f"{what} not found."})

        def _body(self) -> Any:
            length = int(self.headers.get("Content-Length") or 0)
            if not length:
                return None
            try:
                return json.loads(self.rfile.read(length))
            except ValueError:
                return None

        # ------------------------------------------------------------- routing
        def do_GET(self) -> None:  # noqa: N802 - http.server naming
            self._route("GET")

        def do_HEAD(self) -> None:  # noqa: N802
            self._route("GET")

        def do_POST(self) -> None:  # noqa: N802
            self._route("POST")

        def do_DELETE(self) -> None:  # noqa: N802
            self._route("DELETE")

        def _route(self, method: str) -> None:
            path = unquote(urlsplit(self.path).path)
            if path == "/__control/strobe.html":
                self._send(200, STROBE_HTML.encode("utf-8"), TYPES[".html"])
            elif path == "/health":
                self._json(200, {"ok": True, "version": "0.1.0", "engineVersion": "1.0.0"})
            elif path == "/jobs" or path.startswith("/jobs/"):
                with state.lock:
                    self._api(method, path)
            elif method == "GET":
                self._static(path)
            else:
                self._not_found("Endpoint")

        def _api(self, method: str, path: str) -> None:
            if path == "/jobs" and method == "GET":
                self._json(200, state.jobs)
                return
            if path == "/jobs" and method == "POST":
                self._create(self._body() or {})
                return
            match = re.fullmatch(r"/jobs/([^/]+)(/.*)?", path)
            if not match:
                self._not_found("Endpoint")
                return
            job_id, rest = match.group(1), match.group(2) or ""
            job = state.job(job_id)
            if job is None:
                self._not_found(f"Job {job_id}")
                return
            if rest == "" and method == "GET":
                if job_id == IDS["RUNNING"]:
                    state.advance_running(job)
                state.finish_verify(job)
                self._json(200, job)
            elif rest == "" and method == "DELETE":
                state.jobs.remove(job)
                self.send_response(204)
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", "0")
                self.end_headers()
            elif rest == "/report" and method == "GET":
                if job["status"] != "done":
                    if job["status"] == "error":
                        self._not_found("Report")
                    else:
                        self._json(409, {"detail": "The job is not done yet."})
                    return
                report = state.reports.get(job_id)
                if report is None:
                    self._not_found("Report")
                else:
                    self._json(200, report)
            elif rest == "/report.html" and method == "GET":
                self._send(200, b"<!doctype html><title>Report</title>", TYPES[".html"])
            elif (frame := re.fullmatch(r"/frames/(\d+)\.png", rest)) and method == "GET":
                index = int(frame.group(1))
                report = state.reports.get(job_id)
                if report is None or index >= report["capture"]["frameCount"]:
                    self._not_found(f"Frame {index}")
                    return
                self._send(200, state.frame_png(job_id, index), "image/png")
            elif rest == "/ablate" and method == "POST":
                job.update(status="running", phase="ablating", progress={"current": 1, "total": 6, "label": "ablating 1/6"})
                self._json(202, job)
            elif (verify := re.fullmatch(r"/fixes/(\d+)/verify", rest)) and method == "POST":
                state.verify_pending[job_id] = int(verify.group(1))
                job.update(status="running", phase="verifying")
                self._json(202, job)
            else:
                self._not_found("Endpoint")

        def _create(self, body: dict[str, Any]) -> None:
            state.jobs.insert(0, {
                "id": NEW_JOB_ID,
                "url": body.get("url"),
                "createdAt": "2026-10-24T17:00:00Z",
                "status": "queued",
                "phase": "queued",
                "progress": {"current": 0, "total": 0, "label": ""},
                "settings": {
                    "duration": body.get("duration", 10),
                    "viewport": body.get("viewport", {"width": 1024, "height": 768}),
                    "attribute": body.get("attribute", "bbox"),
                },
                "verdict": None,
                "reason": None,
                "error": None,
            })
            self._json(201, {"id": NEW_JOB_ID})

        def _static(self, path: str) -> None:
            candidates = [path + "index.html"] if path.endswith("/") else [path, path + "/index.html", path + ".html"]
            for candidate in candidates:
                full = (out_root / ("." + candidate)).resolve()
                if full != out_root and out_root not in full.parents:
                    break  # never serve outside web/out
                if full.is_file():
                    self._send(200, full.read_bytes(), TYPES.get(full.suffix, "application/octet-stream"))
                    return
            missing = out_root / "404.html"
            body = missing.read_bytes() if missing.is_file() else b"Not found"
            self._send(404, body, TYPES[".html"])

    return Handler


def serve(
    out_dir: str | Path = DEFAULT_OUT,
    fixtures_dir: str | Path = DEFAULT_FIXTURES,
    port: int = 0,
    *,
    frames: str = "square",
    jobs: int | None = None,
) -> tuple[ThreadingHTTPServer, str]:
    """Start a fresh mock server on 127.0.0.1 in a daemon thread; returns (server, base_url).

    ``frames="alternate"`` makes every FAIL frame differ from the one before (F1: every step in the
    frame viewer is a full black and white swap). ``jobs=N`` replaces the job list with N finished
    jobs (F4 uses 24 so the rows that slide up fill the viewport; F4b uses 1). Call
    ``server.shutdown()`` when done.
    """
    out = Path(out_dir)
    if not (out / "index.html").is_file():
        raise FileNotFoundError(f"no static export at {out}: run `npm run build` in web/ first")
    state = MockState(Path(fixtures_dir), frames=frames, jobs=jobs)
    server = ThreadingHTTPServer(("127.0.0.1", port), _make_handler(out, state))
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, name="ui-mock-server", daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve the built FlashTrace UI with a mocked API.")
    parser.add_argument("--port", type=int, default=4318)
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="the static export (web/out)")
    parser.add_argument("--fixtures", default=str(DEFAULT_FIXTURES))
    parser.add_argument("--frames", choices=("square", "alternate"), default="square")
    parser.add_argument("--jobs", type=int, default=None, help="serve this many finished jobs instead of jobs.json")
    args = parser.parse_args()
    server, url = serve(args.out, args.fixtures, args.port, frames=args.frames, jobs=args.jobs)
    print(f"serving the FlashTrace UI with a mocked API on {url} (Ctrl+C stops)")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
