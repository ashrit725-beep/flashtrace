"""``flashtrace`` command-line tool (Typer).

    flashtrace audit URL_OR_PATH [options]   audit one page, write report.json / report.html
    flashtrace demo [--port 8765]            serve the deliberately flashing demo site on localhost
    flashtrace bench [options]               audit the synthetic benchmark and write docs/RESULTS.md
    flashtrace fix REPORT [--apply N] [--verify]
                                             list a report's fixes, print fix N's CSS, or verify it
    flashtrace serve [--port 8001]           run the local web server (loopback only)

Human-readable output goes to stderr and always ends with the disclaimer; stdout carries only the JSON
report (audit --json) or the CSS of one fix (fix --apply N).  Exit codes (SPEC 6.4): 0 PASS, 1 FAIL,
1 INCONCLUSIVE (0 with --no-fail), 2 error; ``fix --verify``: 0 verified, 1 not verified, 2 error.
Errors are one line; --debug adds the traceback.
"""

from __future__ import annotations

import json
import shlex
import sys
import traceback
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Optional, TextIO

import typer

from cli.jobs import AuditSettings, parse_viewport
from cli.report import DISCLAIMER, FIX_NOTE_PREFIX

EXIT_ERROR = 2
REPO_ROOT = Path(__file__).resolve().parent.parent

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    pretty_exceptions_enable=False,
    help=f"FlashTrace: audit web pages against WCAG 2.2 SC 2.3.1 (Three Flashes or Below Threshold). {DISCLAIMER}",
)


class Attribute(str, Enum):
    none = "none"
    bbox = "bbox"
    ablate = "ablate"


class RedRule(str, Enum):
    wcag22 = "wcag22"
    wcag21_legacy = "wcag21-legacy"


class Strategy(str, Enum):
    auto = "auto"
    waapi_step = "waapi_step"
    virtual_time = "virtual_time"
    realtime = "realtime"


def say(text: str = "") -> None:
    """One line of human output (stderr, so stdout stays clean for --json)."""
    print(text, file=sys.stderr, flush=True)


def fail(exc: BaseException | str, debug: bool) -> typer.Exit:
    """Print a one-line error (and the traceback with --debug); return the exit to raise."""
    if debug and isinstance(exc, BaseException):
        traceback.print_exception(exc, file=sys.stderr)
    message = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
    say(f"error: {message}")
    return typer.Exit(code=EXIT_ERROR)


class ProgressPrinter:
    """capture() progress callback: 'frames N/M' on stderr, plus later phases ('attributing 3/6').

    On a terminal one line is rewritten in place; otherwise (logs, pipes) a line is printed about every
    tenth of the capture so the output stays short.  ``phase`` lines are always printed whole.
    """

    def __init__(self, stream: TextIO) -> None:
        self.stream = stream
        self.interactive = stream.isatty()

    def __call__(self, done: int, total: int) -> None:
        if self.interactive:
            end = "\n" if done >= total else ""
            print(f"\rframes {done}/{total}", end=end, file=self.stream, flush=True)
        elif done == total or done % max(1, total // 10) == 0:
            print(f"frames {done}/{total}", file=self.stream, flush=True)

    def phase(self, label: str, done: int | None = None, total: int | None = None) -> None:
        """One line naming a pipeline phase, with a count when there is one ('attributing 3/6')."""
        counts = f" {done}/{total}" if done is not None and total is not None else ""
        print(f"{label}{counts}", file=self.stream, flush=True)


def summary_lines(report: dict[str, Any], job_dir: Path, out: Path | None, html: Path | None) -> list[str]:
    """The short human summary printed after an audit."""
    capture = report["capture"]
    lines = [
        f"{report['verdict']}: {report['reason']}",
        f"capture: strategy {capture['strategy']}, deterministic {'yes' if capture['deterministic'] else 'no'}, "
        f"{capture['frameCount']} frames",
        f"job: {job_dir}",
        f"report: {job_dir / 'report.json'}",
        f"html: {job_dir / 'report.html'}",
    ]
    if out is not None:
        lines.append(f"report copy: {out}")
    if html is not None:
        lines.append(f"html copy: {html}")
    if report["culprits"] or report["fixes"]:
        lines.append(f"culprits: {len(report['culprits'])}, fixes: {len(report['fixes'])} "
                     f"(list them: flashtrace fix {job_dir / 'report.json'})")
    return lines


@app.command()
def audit(
    url_or_path: str = typer.Argument(..., help="http(s) URL, file:// URL or path of a local HTML file."),
    duration: float = typer.Option(10.0, "--duration", help="Seconds of animation time to capture."),
    viewport: str = typer.Option("1024x768", "--viewport", help="Viewport WIDTHxHEIGHT in CSS px."),
    fps: int = typer.Option(60, "--fps", help="Frames per second of animation time."),
    script: Optional[Path] = typer.Option(None, "--script", help="JavaScript run in the page before frame 0."),
    attribute: Attribute = typer.Option(Attribute.bbox, "--attribute",
                                        help="Culprit attribution: none, bbox (cheap ranking) or ablate (re-captures)."),
    out: Optional[Path] = typer.Option(None, "--out", help="Also write the JSON report here."),
    html: Optional[Path] = typer.Option(None, "--html", help="Also write the HTML report here."),
    as_json: bool = typer.Option(False, "--json", help="Print the JSON report on stdout."),
    no_fail: bool = typer.Option(False, "--no-fail", help="Exit 0 on INCONCLUSIVE."),
    red_rule: RedRule = typer.Option(RedRule.wcag22, "--red-rule", help="Red-flash rule (wcag21-legacy is for comparison only)."),
    strategy: Strategy = typer.Option(Strategy.auto, "--strategy", help="Capture strategy."),
    work_dir: Path = typer.Option(Path(".flashtrace"), "--work-dir", help="Where job folders are kept."),
    quiet: bool = typer.Option(False, "--quiet", help="No progress lines."),
    debug: bool = typer.Option(False, "--debug", help="Show tracebacks on errors."),
) -> None:
    """Audit one page against WCAG 2.3.1 and write a report."""
    from cli.audit import run_audit

    try:
        settings = AuditSettings(duration_s=duration, viewport=parse_viewport(viewport), fps=fps,
                                 red_rule=red_rule.value, attribute=attribute.value, strategy=strategy.value,
                                 script=None if script is None else str(script))
        progress = None if quiet else ProgressPrinter(sys.stderr)
        report, job_dir, code = run_audit(url_or_path, settings, work_dir=work_dir, out=out, html_out=html,
                                          no_fail=no_fail, progress=progress)
    except Exception as exc:  # noqa: BLE001 - every failure is reported as one line and exit code 2
        raise fail(exc, debug) from None
    if as_json:
        sys.stdout.write(json.dumps(report, sort_keys=True, indent=2, ensure_ascii=False) + "\n")
        sys.stdout.flush()
    for line in summary_lines(report, job_dir, out, html):
        say(line)
    say(DISCLAIMER)
    raise typer.Exit(code=code)


def load_demo_server() -> Callable[..., None]:
    """demo/serve.py's serve(); demo/ is not a package, so the repository root goes on sys.path first."""
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    from demo.serve import serve as demo_serve

    return demo_serve


@app.command()
def demo(port: int = typer.Option(8765, "--port", help="TCP port on 127.0.0.1.")) -> None:
    """Serve the demo site (it flashes on purpose; the start page is a warning)."""
    try:
        load_demo_server()(port=port)
    except Exception as exc:  # noqa: BLE001
        raise fail(exc, debug=False) from None
    finally:
        say(DISCLAIMER)


@app.command()
def bench(
    n: int = typer.Option(200, "--n", help="Number of benchmark pages."),
    seed: int = typer.Option(1, "--seed", help="Benchmark seed."),
    duration: float = typer.Option(10.0, "--duration", help="Seconds captured per page."),
    fps: int = typer.Option(60, "--fps", help="Frames per second."),
    workers: int = typer.Option(4, "--workers", help="Parallel audit processes."),
    attribute: Attribute = typer.Option(Attribute.none, "--attribute", help="Attribution mode per page."),
    determinism_sample: int = typer.Option(20, "--determinism-sample", help="Pages audited twice to check determinism."),
    out: Path = typer.Option(Path("docs/RESULTS.md"), "--out",
                             help="Markdown results file; the per-page JSON is written next to it as .json."),
    regenerate: bool = typer.Option(False, "--regenerate", help="Regenerate the pages and truth first."),
    pages: Path = typer.Option(Path("bench/pages"), "--pages", help="Benchmark pages folder."),
    truth: Path = typer.Option(Path("bench/truth.json"), "--truth", help="Benchmark truth file."),
    work_dir: Path = typer.Option(Path(".flashtrace/bench"), "--work-dir",
                                  help="Where job folders go, plus a copy of the per-page JSON (results.json)."),
    quiet: bool = typer.Option(False, "--quiet", help="No per-page progress lines."),
    debug: bool = typer.Option(False, "--debug", help="Show tracebacks on errors."),
) -> None:
    """Audit the synthetic benchmark pages and score them against the truth file."""
    from bench.run import run_bench

    command_line = shlex.join(["flashtrace"] + sys.argv[1:])
    try:
        results = run_bench(n=n, seed=seed, duration=duration, fps=fps, workers=workers,
                            attribute=attribute.value, determinism_sample=determinism_sample, out=out,
                            regenerate=regenerate, pages=pages, truth=truth, work_dir=work_dir,
                            command_line=command_line, quiet=quiet)
    except Exception as exc:  # noqa: BLE001
        raise fail(exc, debug) from None
    for line in results["summaryLines"]:
        say(line)
    say(DISCLAIMER)


def measurement_line(name: str, measured: dict[str, Any] | None) -> str:
    """'before: FAIL, general 4/s, red 0/s, qualifying general 11, red 0, deterministic yes'."""
    if measured is None:
        return f"{name}: not measured"
    return (f"{name}: {measured['verdict']}, general {measured['generalMaxRatePerSec']:g}/s, "
            f"red {measured['redMaxRatePerSec']:g}/s, qualifying general {measured['qualifyingGeneral']}, "
            f"red {measured['qualifyingRed']}, deterministic {'yes' if measured['deterministic'] else 'no'}")


def no_fix_reason(report: dict[str, Any]) -> str:
    """Why a report has no fixes, in one line."""
    if report["settings"].get("attribute") == "none":
        return "This report has no fixes: the audit ran without attribution (use --attribute bbox or ablate)."
    if not report.get("violations"):
        return "This report has no fixes: the audit found no violation to fix."
    if not report.get("culprits"):
        return "This report has no fixes: attribution found no culprit element."
    stated = [note[len(FIX_NOTE_PREFIX):] for note in report.get("limitations") or []
              if note.startswith(FIX_NOTE_PREFIX)]
    return f"This report has no fixes: {stated[0] if stated else 'none could be derived from its measurements.'}"


def fix_listing(report: dict[str, Any]) -> list[str]:
    """One line per fix, numbered from 1 as ``--apply`` counts them."""
    if not report["fixes"]:
        return [no_fix_reason(report)]
    lines = []
    for number, item in enumerate(report["fixes"], start=1):
        if item["kind"] == "area":
            state = "not auto-applied"
        else:
            state = "verified" if item["verified"] else ("not verified" if item["after"] is not None else "not tried")
        lines.append(f"{number}. culprit {item['culprit']}, {item['kind']} ({state}): {item['css']}")
    return lines


def load_report_file(path: Path) -> dict[str, Any]:
    """A report.json from disk (ValueError with the file name when it is not a JSON report)."""
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"cannot read report {path}: {exc}") from None
    if not isinstance(report, dict) or "fixes" not in report:
        raise ValueError(f"{path} is not a FlashTrace report")
    return report


def chosen_fix(report: dict[str, Any], number: int) -> dict[str, Any]:
    """Fix ``number`` (from 1); ValueError naming the valid range."""
    count = len(report["fixes"])
    if not 1 <= number <= count:
        raise ValueError(f"--apply {number}: the report has {count} fix(es); use 1 to {count}")
    return report["fixes"][number - 1]


def verify_and_report(report_path: Path, report: dict[str, Any], number: int, work_dir: Path) -> int:
    """Verify fix ``number`` in the report's job, update the given report file, print before/after."""
    from cli.audit import locate_job, verify_fix_in_job
    from cli.report import export_report

    wanted = chosen_fix(report, number)
    job_dir = locate_job(report_path, work_dir)
    current = load_report_file(job_dir / "report.json")
    if len(current["fixes"]) < number or current["fixes"][number - 1]["css"] != wanted["css"]:
        raise ValueError(f"{report_path} no longer matches its job's report; use {job_dir / 'report.json'}")
    say(f"fix {number} ({wanted['kind']}) for culprit {wanted['culprit']}: {wanted['css']}")
    progress = ProgressPrinter(sys.stderr) if wanted["kind"] != "area" else None  # area fixes are not re-captured
    updated = verify_fix_in_job(job_dir, number - 1, progress=progress)
    if report_path.resolve() != (job_dir / "report.json").resolve():
        export_report(updated, job_dir, report_path)
    item = updated["fixes"][number - 1]
    say(measurement_line("before", item["before"]))
    say(measurement_line("after", item["after"]))
    verdict = "yes: PASS under deterministic capture" if item["verified"] else "no"
    say(f"verified: {verdict}")
    if not item["verified"] and item["kind"] == "area":
        say("area fixes change the layout and are never applied automatically")
    say(f"report: {job_dir / 'report.json'}")
    return 0 if item["verified"] else 1


@app.command()
def fix(
    report: Path = typer.Argument(..., help="report.json of an audit run with --attribute bbox or ablate."),
    apply: Optional[int] = typer.Option(None, "--apply", help="Number of the fix (1 = first, as listed without --apply)."),
    verify: bool = typer.Option(False, "--verify", help="Re-capture the page with the fix injected and record the result."),
    work_dir: Path = typer.Option(Path(".flashtrace"), "--work-dir",
                                  help="Where the audit kept its job folders (to find the job of a copied report)."),
    debug: bool = typer.Option(False, "--debug", help="Show tracebacks on errors."),
) -> None:
    """List a report's CSS fixes, print one, or verify it by re-capturing (exit 0 verified, 1 not, 2 error)."""
    try:
        data = load_report_file(report)
        if verify and apply is None:
            raise ValueError("--verify needs --apply N (the number of the fix to verify)")
        if apply is None:
            for line in fix_listing(data):
                say(line)
            code = 0
        elif not verify:
            sys.stdout.write(chosen_fix(data, apply)["css"] + "\n")
            code = 0
        else:
            code = verify_and_report(report, data, apply, work_dir)
    except Exception as exc:  # noqa: BLE001 - one line and exit 2
        raise fail(exc, debug) from None
    say(DISCLAIMER)
    raise typer.Exit(code=code)


@app.command()
def serve(
    port: int = typer.Option(8001, "--port", help="TCP port on 127.0.0.1."),
    work_dir: Optional[Path] = typer.Option(None, "--work-dir", help="Where job folders are kept (default ./.flashtrace)."),
    allow_local_files: bool = typer.Option(False, "--allow-local-files",
                                           help="Allow file:// and local paths outside the project directory."),
    debug: bool = typer.Option(False, "--debug", help="Show tracebacks on errors."),
) -> None:
    """Run the local web server and viewer on 127.0.0.1 (one audit at a time)."""
    try:
        from server.app import serve as run_server  # imported here: FastAPI is not needed by the other commands

        run_server(port=port, work_dir=work_dir, allow_local_files=allow_local_files)
    except Exception as exc:  # noqa: BLE001
        raise fail(exc, debug) from None
    finally:
        say(DISCLAIMER)


if __name__ == "__main__":
    app()
