"""``flashtrace serve``: the JSON API of docs/API.md and the web UI, from one loopback-only process.

    create_app(work_dir, project_root, allow_local_files=False, static_dir=None) -> FastAPI
    serve(port=8001, host="127.0.0.1", work_dir=None, allow_local_files=False)

The API and the statically exported UI (``web/out``) share one origin, so the browser needs no CORS.
Security rules (build brief and docs/API.md):

* the server binds to 127.0.0.1 only; ``serve`` refuses any other host;
* the Host header must be 127.0.0.1 or localhost (stops DNS-rebinding pages from reaching the API);
* POST and DELETE requests that carry an Origin header must come from this server's own origin;
* no CORS unless FLASHTRACE_DEV_CORS=1, which allows only the Next.js dev server on port 3000;
* request bodies with unknown fields are refused, job ids must have the job-id format, frame numbers
  must be whole numbers inside the capture (so no request can name a file outside a job directory);
* only URLs the user submitted are fetched; local files only inside the project directory unless
  ``--allow-local-files``; no credentials are stored (URLs with a password are refused);
* every capture gets a fresh browser context (guaranteed by capture.capture);
* every response forbids being shown in a frame of another page (clickjacking) and MIME sniffing.

Every error is ``{"detail": "<one sentence>"}`` with 400 (bad state), 404 (unknown job), 409 (job busy)
or 422 (invalid input); the security checks add 400 (Host header not 127.0.0.1 or localhost) and 403
(POST or DELETE from another origin).
"""

from __future__ import annotations

import html
import importlib.metadata
import json
import mimetypes
import os
import sys
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi import Path as PathParam
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from starlette.datastructures import Headers
from starlette.middleware.cors import CORSMiddleware
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from cli.jobs import FRAMES_DIR_NAME, REPORT_HTML_NAME, REPORT_JSON_NAME, AuditSettings, default_base
from cli.report import DISCLAIMER
from engine import ENGINE_VERSION
from server.jobs import JobRecord, JobStore, delete_job_files, mark_deleted
from server.runner import Runner, Task
from server.validation import (
    DEV_UI_ORIGINS,
    LOOPBACK_HOST,
    TargetError,
    host_allowed,
    is_job_id,
    origin_allowed,
    validate_target,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_STATIC_DIR = REPO_ROOT / "web" / "out"
DEV_CORS_VARIABLE = "FLASHTRACE_DEV_CORS"
UI_ROUTES: tuple[str, ...] = ("/", "/run/", "/report/", "/history/")
STARTUP_FAILURE_EXIT_CODE = 3  # what uvicorn.run exits with when the server could not start

# docs/API.md, POST /jobs: duration 1-60 s (default 10); viewport sides 200-3840 px (default 1024x768).
MIN_DURATION_S, MAX_DURATION_S, DEFAULT_DURATION_S = 1, 60, 10.0
MIN_SIDE_PX, MAX_SIDE_PX = 200, 3840
DEFAULT_VIEWPORT = (1024, 768)

# Sent with every response.  A page on another site could otherwise show the UI in a frame and trick
# the user into clicking "Verify this fix" or "Re-run attribution with ablation" (clickjacking): the
# framed UI's requests carry this server's own Origin, so the Origin check cannot tell.
SECURITY_HEADERS: tuple[tuple[bytes, bytes], ...] = (
    (b"content-security-policy", b"frame-ancestors 'none'"),
    (b"x-frame-options", b"DENY"),
    (b"x-content-type-options", b"nosniff"),
)

# With nosniff a browser refuses a script or stylesheet served under another type, and on Windows a
# registry entry can make Python's mimetypes answer text/plain for ".js"; the UI build needs these.
UI_MEDIA_TYPES: dict[str, str] = {".js": "text/javascript", ".mjs": "text/javascript", ".css": "text/css"}


def package_version() -> str:
    """The installed flashtrace version (pyproject.toml), for GET /health."""
    try:
        return importlib.metadata.version("flashtrace")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


# --------------------------------------------------------------------------------------- bodies
class ViewportIn(BaseModel):
    """{"width": ..., "height": ...} in CSS px; strict, so true or "1024" are refused, not converted."""

    model_config = ConfigDict(extra="forbid")
    width: int = Field(ge=MIN_SIDE_PX, le=MAX_SIDE_PX, strict=True)
    height: int = Field(ge=MIN_SIDE_PX, le=MAX_SIDE_PX, strict=True)


class JobRequest(BaseModel):
    """Body of POST /jobs.  extra="forbid" makes any unknown field a 422."""

    model_config = ConfigDict(extra="forbid")
    url: str = Field(min_length=1, strict=True)
    duration: float = Field(default=DEFAULT_DURATION_S, ge=MIN_DURATION_S, le=MAX_DURATION_S,
                            strict=True, allow_inf_nan=False)
    viewport: ViewportIn = Field(default_factory=lambda: ViewportIn(width=DEFAULT_VIEWPORT[0], height=DEFAULT_VIEWPORT[1]))
    attribute: Literal["none", "bbox", "ablate"] = "bbox"

    def to_settings(self) -> AuditSettings:
        """The audit settings (fps, red rule and strategy keep the CLI defaults)."""
        return AuditSettings(duration_s=float(self.duration), viewport=(self.viewport.width, self.viewport.height),
                             attribute=self.attribute)


# --------------------------------------------------------------------------------------- state
@dataclass
class ServerState:
    """What every request handler needs; stored on ``app.state.server``."""

    store: JobStore
    runner: Runner
    project_root: Path
    allow_local_files: bool


def server_state(request: Request) -> ServerState:
    return request.app.state.server


def find_record(request: Request, job_id: str) -> JobRecord:
    """The job named in the path: 422 for a malformed id, 404 for an unknown one."""
    if not is_job_id(job_id):
        raise HTTPException(status_code=422, detail=f"{job_id!r} is not a FlashTrace job id.")
    record = server_state(request).store.get(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"There is no job {job_id}.")
    return record


def summarize(request: Request, record: JobRecord) -> dict[str, Any]:
    state = server_state(request)
    return state.store.summary(record, state.runner.jobs_ahead(record.id))


def finished_file(record: JobRecord, name: str) -> Path:
    """A file of a finished job: 409 while the job is queued or running, 404 if it was never written."""
    if record.active:
        raise HTTPException(status_code=409, detail="The job is still queued or running; try again when it is done.")
    path = record.job_dir / name
    if not path.is_file():
        raise HTTPException(status_code=404, detail="This job has no report because it stopped before one was written.")
    return path


def read_report(record: JobRecord) -> dict[str, Any]:
    """report.json of a finished job (409/404 as in finished_file)."""
    return json.loads(finished_file(record, REPORT_JSON_NAME).read_text(encoding="utf-8"))


def frame_count(record: JobRecord) -> int:
    """report.capture.frameCount, read once and cached.

    The frames never change after the first audit (ablation and verification write their re-captures
    into other folders), so they stay available while such a follow-up task runs; only while the first
    audit is still queued or running is the answer 409.
    """
    if record.frame_count is None:
        try:
            record.frame_count = int(read_report(record)["capture"]["frameCount"])
        except ValueError:  # report.json is being rewritten at this very moment
            raise HTTPException(status_code=409, detail="The report is being updated; try again.") from None
    return record.frame_count


def require_follow_up_allowed(record: JobRecord) -> dict[str, Any]:
    """Ablation and verification need a job that is not busy (409) and has a report (400)."""
    if record.active:
        raise HTTPException(status_code=409, detail="The job is busy; wait until it is done.")
    if not (record.job_dir / REPORT_JSON_NAME).is_file():
        raise HTTPException(status_code=400, detail="This job has no report to work on, because it stopped before one was written.")
    report = read_report(record)
    record.frame_count = int(report["capture"]["frameCount"])  # cached so frames stay served during the task
    return report


def queue_follow_up(request: Request, record: JobRecord, task: Task) -> dict[str, Any]:
    """Mark the job queued and hand the task to the runner (in that order, so "running" is never overwritten)."""
    state = server_state(request)
    if not state.store.queue_again(record):
        raise HTTPException(status_code=409, detail="The job is busy; wait until it is done.")
    state.runner.submit(task)
    return summarize(request, record)


# --------------------------------------------------------------------------------------- routes
router = APIRouter()


@router.get("/health")
def health() -> dict[str, Any]:
    return {"ok": True, "version": package_version(), "engineVersion": ENGINE_VERSION}


@router.post("/jobs", status_code=201)
def create_job(body: JobRequest, request: Request) -> dict[str, str]:
    """Validate the target, create the job directory, queue the audit, answer with the new id."""
    state = server_state(request)
    try:
        url = validate_target(body.url, state.project_root, state.allow_local_files)
    except TargetError as exc:
        raise HTTPException(status_code=422, detail=f"url: {exc}.") from None
    record = state.store.create(url, body.to_settings())
    state.runner.submit(Task(record, "audit"))
    return {"id": record.id}


@router.get("/jobs")
def list_all_jobs(request: Request) -> list[dict[str, Any]]:
    return [summarize(request, record) for record in server_state(request).store.newest_first()]


@router.get("/jobs/{job_id}")
def get_job(job_id: str, request: Request) -> dict[str, Any]:
    return summarize(request, find_record(request, job_id))


@router.get("/jobs/{job_id}/report")
def get_report(job_id: str, request: Request) -> FileResponse:
    path = finished_file(find_record(request, job_id), REPORT_JSON_NAME)
    return FileResponse(path, media_type="application/json")


@router.get("/jobs/{job_id}/report.html")
def get_report_html(job_id: str, request: Request) -> FileResponse:
    path = finished_file(find_record(request, job_id), REPORT_HTML_NAME)
    return FileResponse(path, media_type="text/html; charset=utf-8")


@router.get("/jobs/{job_id}/frames/{index}.png")
def get_frame(job_id: str, index: int, request: Request) -> FileResponse:
    """Captured frame ``index`` (0 ... frameCount-1).  The number picks the file; the client never names a path."""
    record = find_record(request, job_id)
    count = frame_count(record)
    if not 0 <= index < count:
        raise HTTPException(status_code=404, detail=f"Frame {index} is outside 0 to {count - 1}.")
    path = record.job_dir / FRAMES_DIR_NAME / f"{index:06d}.png"
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"Frame {index} is missing from the job directory.")
    return FileResponse(path, media_type="image/png")


@router.post("/jobs/{job_id}/ablate", status_code=202)
def ablate_job(job_id: str, request: Request) -> dict[str, Any]:
    record = find_record(request, job_id)
    require_follow_up_allowed(record)
    return queue_follow_up(request, record, Task(record, "ablate"))


@router.post("/jobs/{job_id}/fixes/{n}/verify", status_code=202)
def verify_fix(job_id: str, request: Request, n: int = PathParam(ge=0)) -> dict[str, Any]:
    record = find_record(request, job_id)
    fixes = require_follow_up_allowed(record).get("fixes", [])
    if n >= len(fixes):
        raise HTTPException(status_code=404, detail=f"The report has no fix {n} (it has {len(fixes)}).")
    return queue_follow_up(request, record, Task(record, "verify", fix_index=n))


@router.delete("/jobs/{job_id}", status_code=204)
def delete_job(job_id: str, request: Request) -> Response:
    """Cancel the job if it is queued or running, then delete its directory.

    The folder is marked deleted first, so a running job's folder, which the runner removes only once
    the task has stopped, is removed at the next start if the server stops before that (server/jobs.py).
    """
    state = server_state(request)
    record = find_record(request, job_id)
    state.store.remove(record.id)
    mark_deleted(record.job_dir)
    if record.active:
        state.store.mark_cancelled(record)  # kept on disk only if deleting the folder fails
    if not state.runner.cancel(record):
        delete_job_files(record.job_dir)  # the runner deletes it itself when it is working on this job
    return Response(status_code=204)


# --------------------------------------------------------------------------------------- errors
def validation_sentence(errors: Sequence[Any]) -> str:
    """FastAPI's list of validation errors -> one sentence, e.g. 'duration: Input should be ... 60.'"""
    if not errors:
        return "The request is not valid."
    first = errors[0]
    if first.get("type") == "json_invalid":  # its location is a character offset, not a field name
        return "request body: the body is not valid JSON."
    location = [str(part) for part in first.get("loc", ()) if part not in ("body", "path", "query")]
    where = ".".join(location) or "request body"
    message = str(first.get("msg", "invalid value")).rstrip(".")
    return f"{where}: {message}."


async def on_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    """422 with the one-sentence ``detail`` of docs/API.md instead of FastAPI's list of errors."""
    return JSONResponse(status_code=422, content={"detail": validation_sentence(exc.errors())})


# --------------------------------------------------------------------------------------- middleware
class SameOriginMiddleware:
    """Refuse POST/DELETE requests whose Origin header names another site (403).

    Without CORS a foreign page cannot read our answers, but a plain HTML form could still *send* a
    POST (for example to start an ablation).  Browsers always add an Origin header to such requests.
    """

    UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

    def __init__(self, app: ASGIApp, extra_origins: tuple[str, ...] = ()) -> None:
        self.app = app
        self.extra_origins = extra_origins

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["method"] in self.UNSAFE_METHODS:
            headers = Headers(scope=scope)
            if not origin_allowed(headers.get("origin"), headers.get("host"), self.extra_origins):
                response = JSONResponse(status_code=403, content={"detail": "Requests from other websites are not accepted."})
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


class AllowedHostMiddleware:
    """Refuse requests whose Host header is not 127.0.0.1 or localhost: 400 with the usual JSON ``detail``.

    A DNS-rebinding page reaches 127.0.0.1 under its own domain name; the Host header gives it away.
    (Starlette's TrustedHostMiddleware makes the same check but answers in plain text.)
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and not host_allowed(Headers(scope=scope).get("host")):
            response = JSONResponse(status_code=400, content={"detail": "The Host header must be 127.0.0.1 or localhost."})
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


class SecurityHeadersMiddleware:
    """Add SECURITY_HEADERS to every HTTP response, errors included."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                message = {**message, "headers": [*message.get("headers", []), *SECURITY_HEADERS]}
            await send(message)

        await self.app(scope, receive, send_with_headers)


def dev_cors_enabled() -> bool:
    return os.environ.get(DEV_CORS_VARIABLE) == "1"


def add_security_middleware(app: FastAPI) -> None:
    """Origin check (innermost), Host check, CORS for the dev UI only, then the headers (outermost)."""
    dev_origins = DEV_UI_ORIGINS if dev_cors_enabled() else ()
    app.add_middleware(SameOriginMiddleware, extra_origins=dev_origins)
    app.add_middleware(AllowedHostMiddleware)
    if dev_origins:
        app.add_middleware(CORSMiddleware, allow_origins=list(dev_origins), allow_methods=["GET", "POST", "DELETE"],
                           allow_headers=["Accept", "Content-Type"])
    app.add_middleware(SecurityHeadersMiddleware)  # outermost, so the refusals above carry the headers too


# --------------------------------------------------------------------------------------- web UI
UI_MISSING_PAGE = f"""<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>FlashTrace</title></head>
<body>
<main>
<h1>FlashTrace server is running</h1>
<p>The web UI has not been built yet.</p>
<p>Build the UI with: cd web &amp;&amp; npm run build</p>
<p>Then restart <code>flashtrace serve</code>.</p>
<p>The JSON API is available now, for example at <a href="/health">/health</a> and <code>/jobs</code> (see docs/API.md).</p>
</main>
<footer><p>{html.escape(DISCLAIMER)}</p></footer>
</body>
</html>
"""


def ui_missing() -> HTMLResponse:
    return HTMLResponse(UI_MISSING_PAGE)


def mount_ui(app: FastAPI, static_dir: Path) -> None:
    """Serve the static UI export at '/' (registered after the API routes, so those win).

    html=True serves ``run/index.html`` for '/run/' and redirects '/run' to '/run/' (query kept).
    Without a build, the UI routes answer with a short page that says how to build it.
    """
    if (static_dir / "index.html").is_file():
        for suffix, media_type in UI_MEDIA_TYPES.items():
            mimetypes.add_type(media_type, suffix)  # StaticFiles looks the type up per file
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="ui")
        return
    for route in UI_ROUTES:
        app.add_api_route(route, ui_missing, methods=["GET"], response_class=HTMLResponse, include_in_schema=False)


# --------------------------------------------------------------------------------------- app
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Nothing to do at start (the runner thread starts with the first job); stop the runner at exit."""
    yield
    app.state.server.runner.stop()


def create_app(
    work_dir: Path,
    project_root: Path,
    allow_local_files: bool = False,
    static_dir: Path | None = None,
) -> FastAPI:
    """Build the server.  Reading the job index from ``<work_dir>/jobs`` happens here, once.

    ``project_root`` is the directory local files must lie in (unless ``allow_local_files``);
    ``static_dir`` defaults to this repository's ``web/out``.
    """
    store = JobStore(Path(work_dir))
    app = FastAPI(title="FlashTrace", version=package_version(), description=DISCLAIMER,
                  lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.server = ServerState(store=store, runner=Runner(store), project_root=Path(project_root).resolve(),
                                   allow_local_files=allow_local_files)
    app.add_exception_handler(RequestValidationError, on_validation_error)  # type: ignore[arg-type]
    add_security_middleware(app)
    app.include_router(router)
    mount_ui(app, Path(static_dir) if static_dir is not None else DEFAULT_STATIC_DIR)
    return app


def build_uvicorn_server(app: FastAPI, host: str, port: int) -> Any:
    """A uvicorn.Server whose Ctrl-C handler tells the job runner first.

    uvicorn's own handler only asks the server to exit; the app's lifespan stops the runner later, after
    the connections are closed.  But a terminal's Ctrl-C also kills Chromium at once, so the running
    audit must learn about the stop right away - otherwise its failed capture would be saved as a "done"
    INCONCLUSIVE report and the next queued job would start (see server/runner.py).
    """
    import uvicorn  # imported here so that importing this module does not load the web server

    runner: Runner = app.state.server.runner

    class FlashTraceServer(uvicorn.Server):
        def handle_exit(self, sig: int, frame: Any) -> None:  # uvicorn installs this for SIGINT and SIGTERM
            runner.begin_shutdown()
            super().handle_exit(sig, frame)

    return FlashTraceServer(uvicorn.Config(app, host=host, port=port, log_level="warning"))


def serve(
    port: int = 8001,
    host: str = LOOPBACK_HOST,
    work_dir: Path | None = None,
    allow_local_files: bool = False,
) -> None:
    """Run the server on http://127.0.0.1:<port>/ until Ctrl-C.  Any other host is refused.

    Local files are checked against the current directory (the project being audited); jobs go to
    ``<work_dir>/jobs`` (default ``./.flashtrace``, the same place ``flashtrace audit`` uses).
    """
    if host != LOOPBACK_HOST:
        raise ValueError(f"refusing to bind {host!r}: the FlashTrace server listens on {LOOPBACK_HOST} only")
    base = Path(work_dir) if work_dir is not None else default_base()
    app = create_app(base, project_root=Path.cwd(), allow_local_files=allow_local_files)
    server = build_uvicorn_server(app, host, port)
    print(f"FlashTrace server: http://{host}:{port}/  (jobs in {base / 'jobs'})", file=sys.stderr, flush=True)
    print("Press Ctrl-C to stop.", file=sys.stderr, flush=True)
    print(DISCLAIMER, file=sys.stderr, flush=True)
    try:
        server.run()
    except KeyboardInterrupt:  # after a clean shutdown uvicorn raises the Ctrl-C again; it has been handled
        pass
    if not server.started:  # for example the port is in use; uvicorn has already said so
        sys.exit(STARTUP_FAILURE_EXIT_CODE)
