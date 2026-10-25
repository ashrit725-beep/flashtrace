"""``flashtrace`` command-line tool (Typer).

    flashtrace audit URL_OR_PATH [options]   audit one page, write report.json / report.html
    flashtrace demo [--port 8765]            serve the deliberately flashing demo site on localhost
    flashtrace bench [options]               audit the synthetic benchmark and write docs/RESULTS.md
    flashtrace fix REPORT --apply N --verify (milestone M6)
    flashtrace serve [--port 8001]           (milestone M8)

Human-readable output goes to stderr and always ends with the disclaimer; stdout carries only the JSON
report (audit --json).  Exit codes (SPEC 6.4): 0 PASS, 1 FAIL, 1 INCONCLUSIVE (0 with --no-fail),
2 error.  Errors are one line; --debug adds the traceback.
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
from cli.report import DISCLAIMER

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
    """capture() progress callback: 'frames N/M' on stderr.

    On a terminal one line is rewritten in place; otherwise (logs, pipes) a line is printed about every
    tenth of the capture so the output stays short.
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
    return lines


@app.command()
def audit(
    url_or_path: str = typer.Argument(..., help="http(s) URL, file:// URL or path of a local HTML file."),
    duration: float = typer.Option(10.0, "--duration", help="Seconds of animation time to capture."),
    viewport: str = typer.Option("1024x768", "--viewport", help="Viewport WIDTHxHEIGHT in CSS px."),
    fps: int = typer.Option(60, "--fps", help="Frames per second of animation time."),
    script: Optional[Path] = typer.Option(None, "--script", help="JavaScript run in the page before frame 0."),
    attribute: Attribute = typer.Option(Attribute.none, "--attribute", help="Culprit attribution mode."),
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


@app.command()
def fix(
    report: Path = typer.Argument(..., help="report.json to fix."),
    apply: Optional[int] = typer.Option(None, "--apply", help="Rank of the fix to apply."),
    verify: bool = typer.Option(False, "--verify", help="Re-capture to verify the fix."),
) -> None:
    """Generate and verify CSS fixes (milestone M6)."""
    say("fix generation arrives in milestone M6")
    say(DISCLAIMER)
    raise typer.Exit(code=EXIT_ERROR)


@app.command()
def serve(port: int = typer.Option(8001, "--port", help="TCP port on 127.0.0.1.")) -> None:
    """Run the local web server (milestone M8)."""
    say("server arrives in milestone M8")
    say(DISCLAIMER)
    raise typer.Exit(code=EXIT_ERROR)


if __name__ == "__main__":
    app()
