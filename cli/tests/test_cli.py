"""Tests for the ``flashtrace`` command line (cli/main.py) and the audit pipeline's input checks.

The browser tests run the real tool in a subprocess (``python -m cli.main``) on the capture test pages,
so they cover argument parsing, capture, analysis, report writing and exit codes together.
"""

from __future__ import annotations

import importlib.util
import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

from cli.audit import (
    ATTRIBUTION_UNAVAILABLE,
    AttributionUnavailable,
    AuditInputError,
    exit_code_for,
    run_audit,
)
from cli.jobs import AuditSettings
from cli.main import ProgressPrinter, load_demo_server
from cli.report import DISCLAIMER

REPO_ROOT = Path(__file__).resolve().parents[2]
PAGES = REPO_ROOT / "capture" / "pages"
HAS_ATTRIBUTION = importlib.util.find_spec("capture.attribute") is not None


def flashtrace(*args: str, work_dir: Path) -> subprocess.CompletedProcess[str]:
    """Run the CLI exactly as a user would, with job folders under ``work_dir``."""
    command = [sys.executable, "-m", "cli.main", *args]
    if args and args[0] == "audit":
        command += ["--work-dir", str(work_dir)]
    return subprocess.run(command, cwd=REPO_ROOT, capture_output=True, text=True, timeout=600)


def stderr_lines(result: subprocess.CompletedProcess[str]) -> list[str]:
    return [line for line in result.stderr.splitlines() if line.strip()]


# --------------------------------------------------------------------------------------- no browser
def test_exit_codes() -> None:
    assert exit_code_for("PASS") == 0
    assert exit_code_for("FAIL") == 1 and exit_code_for("FAIL", no_fail=True) == 1
    assert exit_code_for("INCONCLUSIVE") == 1 and exit_code_for("INCONCLUSIVE", no_fail=True) == 0


def test_missing_file_is_one_line_exit_2(tmp_path: Path) -> None:
    result = flashtrace("audit", str(tmp_path / "missing.html"), work_dir=tmp_path)
    assert result.returncode == 2
    assert len(stderr_lines(result)) == 1
    assert stderr_lines(result)[0].startswith("error: ") and "missing.html" in result.stderr
    assert "Traceback" not in result.stderr and result.stdout == ""
    assert not (tmp_path / "jobs").exists()


def test_unknown_attribute_value_exits_2(tmp_path: Path) -> None:
    result = flashtrace("audit", str(PAGES / "static.html"), "--attribute", "nonsense", work_dir=tmp_path)
    assert result.returncode == 2


def test_bad_viewport_exits_2(tmp_path: Path) -> None:
    result = flashtrace("audit", str(PAGES / "static.html"), "--viewport", "wide", work_dir=tmp_path)
    assert result.returncode == 2 and len(stderr_lines(result)) == 1 and "viewport" in result.stderr


def test_huge_duration_exits_2_before_any_job(tmp_path: Path) -> None:
    # 1e308 s is finite but 1e308 x 60 fps is not: a bad option (exit 2), never an INCONCLUSIVE report.
    for flags in (["--duration", "1e308"], ["--duration", "1e308", "--no-fail"]):
        result = flashtrace("audit", str(PAGES / "static.html"), *flags, "--quiet", work_dir=tmp_path)
        assert result.returncode == 2, result.stderr
        assert stderr_lines(result)[0].startswith("error: ") and "too many frames" in result.stderr
        assert "INCONCLUSIVE" not in result.stdout + result.stderr
    assert not (tmp_path / "jobs").exists()


@pytest.mark.skipif(HAS_ATTRIBUTION, reason="capture.attribute exists now")
def test_attribution_not_available_yet(tmp_path: Path) -> None:
    result = flashtrace("audit", str(PAGES / "static.html"), "--attribute", "bbox", work_dir=tmp_path)
    assert result.returncode == 2
    assert stderr_lines(result) == [f"error: {ATTRIBUTION_UNAVAILABLE}"]


def test_fix_and_serve_are_not_built_yet(tmp_path: Path) -> None:
    fix = flashtrace("fix", "report.json", "--apply", "1", "--verify", work_dir=tmp_path)
    assert fix.returncode == 2 and "milestone M6" in fix.stderr and stderr_lines(fix)[-1] == DISCLAIMER
    serve = flashtrace("serve", work_dir=tmp_path)
    assert serve.returncode == 2 and "milestone M8" in serve.stderr and stderr_lines(serve)[-1] == DISCLAIMER


def test_demo_server_can_be_imported_without_starting() -> None:
    serve = load_demo_server()
    assert callable(serve) and serve.__module__ == "demo.serve"


def test_run_audit_rejects_bad_input_before_creating_a_job(tmp_path: Path) -> None:
    with pytest.raises(AuditInputError):
        run_audit(str(tmp_path / "nope.html"), AuditSettings(), work_dir=tmp_path)
    with pytest.raises(AuditInputError):
        run_audit(str(PAGES / "static.html"), AuditSettings(fps=0), work_dir=tmp_path)
    with pytest.raises(AuditInputError, match="too many frames"):
        run_audit(str(PAGES / "static.html"), AuditSettings(duration_s=1e308), work_dir=tmp_path)
    with pytest.raises(AuditInputError):
        run_audit(str(PAGES / "static.html"), AuditSettings(script=str(tmp_path / "no.js")), work_dir=tmp_path)
    with pytest.raises(AuditInputError):
        run_audit((tmp_path / "gone.html").as_uri(), AuditSettings(), work_dir=tmp_path)
    if not HAS_ATTRIBUTION:
        with pytest.raises(AttributionUnavailable):
            run_audit(str(PAGES / "static.html"), AuditSettings(attribute="ablate"), work_dir=tmp_path)
    assert not (tmp_path / "jobs").exists()


def test_progress_printer_writes_short_lines_off_a_terminal() -> None:
    stream = io.StringIO()
    printer = ProgressPrinter(stream)
    for done in range(1, 91):
        printer(done, 90)
    lines = stream.getvalue().splitlines()
    assert lines[0] == "frames 9/90" and lines[-1] == "frames 90/90" and len(lines) == 10


# --------------------------------------------------------------------------------------- browser
@pytest.mark.browser
def test_css_keyframes_page_fails(tmp_path: Path) -> None:
    out, html = tmp_path / "out" / "report.json", tmp_path / "out" / "report.html"
    result = flashtrace("audit", str(PAGES / "css_keyframes.html"), "--duration", "1.5", "--quiet",
                        "--out", str(out), "--html", str(html), work_dir=tmp_path)
    assert result.returncode == 1, result.stderr
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["verdict"] == "FAIL"
    general = [v for v in report["violations"] if v["type"] == "general"]
    # The 400x300 px box flashes 4 times a second (flashes complete at frames 15, 30, 45, 60, ...).
    # The page's 200x200 px fade (0.5 Hz, dark grey <-> white) is itself a large general flash and
    # completes one more inside the same second, so the engine may report 5.
    assert general and general[0]["maxRatePerSec"] >= 4
    assert {15, 30, 45, 60} <= set(general[0]["frameIndices"])
    for frame in report["evidenceFrames"]:
        assert (out.parent / frame["pngPath"]).is_file()
    assert "<strong>FAIL</strong>" in html.read_text(encoding="utf-8")
    assert stderr_lines(result)[0].startswith("FAIL: ") and stderr_lines(result)[-1] == DISCLAIMER


@pytest.mark.browser
def test_static_page_passes_with_json_on_stdout(tmp_path: Path) -> None:
    # Two seconds: SPEC 6.4 needs at least 2*fps frames before a PASS can be reported.
    result = flashtrace("audit", str(PAGES / "static.html"), "--duration", "2", "--json", work_dir=tmp_path)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["verdict"] == "PASS" and report["capture"]["deterministic"] is True
    assert "frames 120/120" in result.stderr and stderr_lines(result)[-1] == DISCLAIMER
    job = Path(next(line for line in stderr_lines(result) if line.startswith("job: "))[5:])
    assert json.loads((job / "report.json").read_text(encoding="utf-8")) == report
    assert json.loads((job / "meta.json").read_text(encoding="utf-8"))["status"] == "done"


@pytest.mark.browser
def test_one_second_capture_is_too_short_for_pass(tmp_path: Path) -> None:
    result = flashtrace("audit", str(PAGES / "static.html"), "--duration", "1", "--json", "--quiet", work_dir=tmp_path)
    assert result.returncode == 1, result.stderr
    report = json.loads(result.stdout)
    assert report["verdict"] == "INCONCLUSIVE" and "only 60 frames" in report["reason"]


@pytest.mark.browser
def test_realtime_capture_is_inconclusive_unless_no_fail(tmp_path: Path) -> None:
    result = flashtrace("audit", str(PAGES / "static.html"), "--strategy", "realtime", "--duration", "2",
                        "--json", "--quiet", work_dir=tmp_path)
    assert result.returncode == 1, result.stderr
    report = json.loads(result.stdout)
    assert report["verdict"] == "INCONCLUSIVE" and report["capture"]["strategy"] == "realtime"
    assert "non-deterministic capture" in report["reason"]
    relaxed = flashtrace("audit", str(PAGES / "static.html"), "--strategy", "realtime", "--duration", "1",
                         "--no-fail", "--quiet", work_dir=tmp_path)
    assert relaxed.returncode == 0, relaxed.stderr
    assert stderr_lines(relaxed)[0].startswith("INCONCLUSIVE: ")
