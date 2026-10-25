"""Tests for ``flashtrace fix`` and the finished-job entry points in cli/audit.py (contract items 4-6).

The synthetic job is a 2 s full-frame 4 Hz black/white square wave with a hand-made culprit, so most
tests need no browser: listing fixes, printing one, the error exits, the area fix (never re-captured,
so ``--verify`` exits 1 at once), finding the job of a copied report, and ``reattribute`` with a stand-in
attributor.  One browser test verifies a real fix through the command line (exit 0).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from capture.fix import propose_fixes
from capture.frames import FrameSequence
from cli.audit import AuditInputError, _start_job, locate_job, reattribute, run_audit, verify_fix_in_job
from cli.jobs import AuditSettings, read_meta, update_meta, write_meta
from cli.report import DISCLAIMER, build_report, export_report, validate_report, write_report
from cli.tests.helpers import FPS, HEIGHT, WIDTH, AnalysedJob, FakeCaptureMeta, analysed_job, square_wave

REPO_ROOT = Path(__file__).resolve().parents[2]
PAGES = REPO_ROOT / "capture" / "pages"
FULL_FRAME = {"x": 0.0, "y": 0.0, "w": float(WIDTH), "h": float(HEIGHT)}
CULPRIT = {"rank": 1, "selector": "#strobe", "nodeType": "canvas", "ftId": None, "rect": FULL_FRAME,
           "animation": None, "overlap": 1.0, "ablationDelta": None, "inMinimalSet": False}


def flashtrace(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-m", "cli.main", *args], cwd=REPO_ROOT, capture_output=True,
                          text=True, timeout=600)


def lines(result: subprocess.CompletedProcess[str]) -> list[str]:
    return [line for line in result.stderr.splitlines() if line.strip()]


def finished_job(base: Path) -> tuple[AnalysedJob, dict[str, Any]]:
    """A synthetic job written the way run_audit writes one: meta.json, report.json with fixes."""
    job = analysed_job(base, square_wave(4), 2 * FPS)
    settings = AuditSettings(duration_s=2.0, viewport=(WIDTH, HEIGHT), fps=FPS, attribute="bbox")
    capture = FakeCaptureMeta(frame_count=job.analysis.frame_count)
    fixes = propose_fixes([CULPRIT], job.analysis, settings, job.job_dir, capture=capture.to_dict())
    report = build_report(job.url, settings, capture, job.analysis, job.job_dir, culprits=[CULPRIT], fixes=fixes,
                          captured_at="2026-10-24T12:00:00Z")
    validate_report(report)
    write_report(report, job.job_dir / "report.json")
    write_meta(job.job_dir, {"jobId": job.job_dir.name, "jobDir": str(job.job_dir), "url": job.url,
                             "settings": settings.to_dict(), "status": "done"})
    return job, report


@pytest.fixture()
def job(tmp_path: Path) -> tuple[AnalysedJob, dict[str, Any]]:
    return finished_job(tmp_path)


# --------------------------------------------------------------------------------------- no browser
def test_synthetic_culprit_gets_filter_and_area_fixes(job: tuple[AnalysedJob, dict[str, Any]]) -> None:
    _job, report = job
    assert [f["kind"] for f in report["fixes"]] == ["luminance", "area"]
    assert report["fixes"][0]["css"].startswith("#strobe { filter: contrast(")


def test_fix_lists_fixes_and_prints_one(job: tuple[AnalysedJob, dict[str, Any]]) -> None:
    analysed, report = job
    path = analysed.job_dir / "report.json"
    listing = flashtrace("fix", str(path))
    assert listing.returncode == 0, listing.stderr
    assert lines(listing)[0].startswith("1. culprit 1, luminance (not tried): #strobe { filter: contrast(")
    assert lines(listing)[1].startswith("2. culprit 1, area (not auto-applied): /* not auto-applied")
    assert lines(listing)[-1] == DISCLAIMER and listing.stdout == ""
    printed = flashtrace("fix", str(path), "--apply", "1")
    assert printed.returncode == 0 and printed.stdout == report["fixes"][0]["css"] + "\n"


def test_fix_errors_exit_2(job: tuple[AnalysedJob, dict[str, Any]], tmp_path: Path) -> None:
    path = job[0].job_dir / "report.json"
    out_of_range = flashtrace("fix", str(path), "--apply", "3")
    assert out_of_range.returncode == 2
    assert lines(out_of_range) == ["error: --apply 3: the report has 2 fix(es); use 1 to 2", DISCLAIMER]
    no_number = flashtrace("fix", str(path), "--verify")
    assert no_number.returncode == 2 and "--verify needs --apply N" in no_number.stderr
    not_a_report = tmp_path / "x.json"
    not_a_report.write_text("[1, 2]", encoding="utf-8")
    assert flashtrace("fix", str(not_a_report)).returncode == 2
    orphan = tmp_path / "orphan.json"
    orphan.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    lost = flashtrace("fix", str(orphan), "--apply", "1", "--verify", "--work-dir", str(tmp_path / "elsewhere"))
    assert lost.returncode == 2 and "no job found" in lost.stderr


def test_area_fix_is_never_verified_exit_1(job: tuple[AnalysedJob, dict[str, Any]]) -> None:
    path = job[0].job_dir / "report.json"
    result = flashtrace("fix", str(path), "--apply", "2", "--verify")
    assert result.returncode == 1, result.stderr
    assert "verified: no" in result.stderr and "never applied automatically" in result.stderr
    saved = json.loads(path.read_text(encoding="utf-8"))
    validate_report(saved)
    assert saved["fixes"][1]["verified"] is False and saved["fixes"][1]["after"] is None
    assert "never verifies it by re-capture" in saved["fixes"][1]["rationale"]
    assert (job[0].job_dir / "report.html").is_file()


def test_copied_report_finds_its_job(job: tuple[AnalysedJob, dict[str, Any]], tmp_path: Path) -> None:
    analysed, report = job
    base = analysed.job_dir.parent.parent  # <base>/jobs/<id>
    copy = tmp_path / "exports" / "copy.json"
    export_report(report, analysed.job_dir, copy)
    assert locate_job(analysed.job_dir / "report.json") == analysed.job_dir
    assert locate_job(copy, base) == analysed.job_dir           # same url, capture time and frames
    update_meta(analysed.job_dir, reportCopies=[str(copy.resolve())])
    assert locate_job(copy, base) == analysed.job_dir           # listed under reportCopies
    with pytest.raises(FileNotFoundError):
        locate_job(copy, tmp_path / "no-such-base")


def test_verify_fix_in_job_writes_the_report(job: tuple[AnalysedJob, dict[str, Any]]) -> None:
    analysed, _report = job
    updated = verify_fix_in_job(analysed.job_dir, 1)  # the area fix: no capture needed
    assert json.loads((analysed.job_dir / "report.json").read_text(encoding="utf-8")) == updated


def test_reattribute_uses_stored_frames(job: tuple[AnalysedJob, dict[str, Any]], monkeypatch: pytest.MonkeyPatch) -> None:
    analysed, report = job
    calls: list[dict[str, Any]] = []

    def fake_attribute(url: str, analysis: Any, capture_meta: Any, settings: AuditSettings, job_dir: Path,
                       mode: str, *, browser: Any = None, progress: Any = None) -> list[dict[str, Any]]:
        calls.append({"url": url, "mode": mode, "violations": len(analysis.violations),
                      "frames": capture_meta.frame_count, "attribute": settings.attribute})
        progress(1, 2, "ablation 1: suppressed ['#strobe']")  # capture.attribute's (done, planned, label)
        (job_dir / "attribution.json").write_text(json.dumps(
            {"limitations": ["attribution: no minimal set within the top 6 candidates"], "error": None}), encoding="utf-8")
        return [dict(CULPRIT, ablationDelta=4.0, inMinimalSet=True)]

    monkeypatch.setattr("cli.audit.find_attributor", lambda mode: fake_attribute)
    phases: list[tuple[str, Any, Any]] = []
    recorder = SimpleNamespace(phase=lambda label, done=None, total=None: phases.append((label, done, total)))
    updated = reattribute(analysed.job_dir, "ablate", progress=recorder)
    assert ("attributing", 1, 2) in phases  # the attributor's three-argument progress reaches the phase hook
    assert calls == [{"url": report["url"], "mode": "ablate", "violations": 1, "frames": 2 * FPS, "attribute": "ablate"}]
    assert updated["settings"]["attribute"] == "ablate" and updated["culprits"][0]["inMinimalSet"] is True
    assert [f["kind"] for f in updated["fixes"]] == ["luminance", "area"]
    assert "attribution: no minimal set within the top 6 candidates" in updated["limitations"]
    assert {k: updated[k] for k in ("verdict", "violations", "evidenceFrames")} == \
           {k: report[k] for k in ("verdict", "violations", "evidenceFrames")}
    assert json.loads((analysed.job_dir / "report.json").read_text(encoding="utf-8")) == updated
    assert read_meta(analysed.job_dir)["settings"]["attribute"] == "ablate"  # type: ignore[index]
    with pytest.raises(AuditInputError):
        reattribute(analysed.job_dir, "none")


def test_run_audit_hands_its_browser_to_the_attributor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # A caller that passes its own Chromium runs Playwright in this thread already; attribution must
    # reuse that browser, because the sync API cannot start a second Playwright in the same thread.
    source = analysed_job(tmp_path / "source", square_wave(4), 2 * FPS)
    page = tmp_path / "page.html"
    page.write_text("<!doctype html><title>t</title>", encoding="utf-8")
    browser = object()  # stands in for a playwright Browser; nothing calls it here
    seen: dict[str, Any] = {}

    def fake_capture(url: str, settings: AuditSettings, script: str | None, job_dir: Path, progress: Any,
                     given: Any) -> tuple[Any, None]:
        seen["capture"] = given
        frames = FrameSequence(source.job_dir / "frames")
        return SimpleNamespace(frames=frames, meta=FakeCaptureMeta(frame_count=len(frames))), None

    def fake_attribute(url: str, analysis: Any, capture_meta: Any, settings: AuditSettings, job_dir: Path,
                       mode: str, *, browser: Any = None, progress: Any = None) -> list[dict[str, Any]]:
        seen["attribute"] = browser
        return [dict(CULPRIT)]

    monkeypatch.setattr("cli.audit._capture_step", fake_capture)
    monkeypatch.setattr("cli.audit.find_attributor", lambda mode: fake_attribute)
    report, _job_dir, _code = run_audit(str(page), AuditSettings(duration_s=2.0, viewport=(WIDTH, HEIGHT), fps=FPS,
                                                                 attribute="bbox"), work_dir=tmp_path / "work",
                                        browser=browser)
    assert seen == {"capture": browser, "attribute": browser}
    assert report["verdict"] == "FAIL" and report["culprits"][0]["selector"] == "#strobe"


def test_follow_up_steps_hand_their_browser_on(job: tuple[AnalysedJob, dict[str, Any]],
                                               monkeypatch: pytest.MonkeyPatch) -> None:
    # reattribute and verify_fix_in_job re-capture too, so like run_audit they must reuse a caller's browser.
    analysed, _report = job
    browser = object()
    seen: dict[str, Any] = {}

    def fake_attribute(url: str, analysis: Any, capture_meta: Any, settings: AuditSettings, job_dir: Path,
                       mode: str, *, browser: Any = None, progress: Any = None) -> list[dict[str, Any]]:
        seen["attribute"] = browser
        return [dict(CULPRIT)]

    def fake_verify(report: dict[str, Any], n: int, job_dir: Path, url: str, settings: AuditSettings, *,
                    progress: Any = None, browser: Any = None) -> dict[str, Any]:
        seen["verify"] = browser
        return report

    monkeypatch.setattr("cli.audit.find_attributor", lambda mode: fake_attribute)
    monkeypatch.setattr("capture.fix.verify_fix", fake_verify)
    reattribute(analysed.job_dir, "bbox", browser=browser)
    verify_fix_in_job(analysed.job_dir, 0, browser=browser)
    assert seen == {"attribute": browser, "verify": browser}


def test_job_start_keeps_settings_and_script(tmp_path: Path) -> None:
    script = tmp_path / "click.js"
    script.write_text("document.body.click();", encoding="utf-8")
    settings = AuditSettings(duration_s=3.0, attribute="bbox", script=str(script))
    job_dir = _start_job("file:///page.html", settings, tmp_path / "work", script.read_text(encoding="utf-8"))
    meta = read_meta(job_dir)
    assert meta["jobDir"] == str(job_dir.resolve()) and meta["url"] == "file:///page.html"
    assert meta["settings"] == dict(settings.to_dict(), script=str(script.resolve()))  # type: ignore[arg-type]
    assert (job_dir / "script.js").read_text(encoding="utf-8") == "document.body.click();"


# --------------------------------------------------------------------------------------- browser
@pytest.mark.browser
def test_fix_verify_exits_0_when_the_fix_passes(tmp_path: Path) -> None:
    # Both steps run exactly as a user runs them: two flashtrace commands, the second one finding the
    # audit's job from the --out copy of its report and --work-dir, then updating that copy too.
    copy = tmp_path / "copy" / "report.json"
    audited = flashtrace("audit", str(PAGES / "css_keyframes.html"), "--duration", "3", "--attribute", "bbox",
                         "--work-dir", str(tmp_path), "--out", str(copy), "--quiet")
    assert audited.returncode == 1, audited.stderr  # FAIL
    report = json.loads(copy.read_text(encoding="utf-8"))
    job_dir = locate_job(copy, tmp_path)
    number = 1 + next(i for i, f in enumerate(report["fixes"]) if f["kind"] == "rate")
    result = flashtrace("fix", str(copy), "--apply", str(number), "--verify", "--work-dir", str(tmp_path))
    assert result.returncode == 0, result.stderr
    assert "after: PASS" in result.stderr and "verified: yes" in result.stderr and lines(result)[-1] == DISCLAIMER
    saved = json.loads((job_dir / "report.json").read_text(encoding="utf-8"))
    assert saved["fixes"][number - 1]["verified"] is True
    assert json.loads(copy.read_text(encoding="utf-8"))["fixes"][number - 1]["verified"] is True  # the copy too
