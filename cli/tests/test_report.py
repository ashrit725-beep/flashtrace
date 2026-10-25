"""Tests for cli/report.py: schema validity, verdict escalation (ADR-008), evidence, notes, output."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from jsonschema.exceptions import ValidationError

from cli.jobs import AuditSettings
from cli.report import (
    DISCLAIMER,
    NON_DETERMINISTIC_NOTE,
    SPEC_LIMITATIONS,
    build_report,
    capture_keys,
    export_report,
    report_fingerprint,
    report_json_text,
    standard_assumptions,
    validate_report,
    write_report,
)
from cli.tests.helpers import (
    FPS,
    AnalysedJob,
    FakeCaptureMeta,
    FakeCaptureMetaWithReportDict,
    analysed_job,
    report_for,
    square_wave,
)
from engine import ENGINE_VERSION
from engine.cells import window_px
from engine.types import PASS_REASON

SPEC_PATH = Path(__file__).resolve().parents[2] / "docs" / "SPEC.md"
NONDETERMINISTIC = FakeCaptureMeta(strategy="realtime", deterministic=False)


# --------------------------------------------------------------------------------------- schema + content
def test_fail_report_is_schema_valid(fail_job: AnalysedJob) -> None:
    report = report_for(fail_job)
    validate_report(report)
    assert report["version"] == "1.0"
    assert report["engineVersion"] == ENGINE_VERSION
    assert report["disclaimer"] == DISCLAIMER
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", build_report(
        fail_job.url, fail_job.settings, FakeCaptureMeta(), fail_job.analysis, fail_job.job_dir)["capturedAt"])
    assert report["settings"]["windowPx"] == {"width": 341, "height": 256}
    assert report["settings"]["cellPx"] == 4


def test_fail_report_has_the_engine_findings(fail_job: AnalysedJob) -> None:
    report = report_for(fail_job)
    assert report["verdict"] == "FAIL"
    assert [(v["type"], v["maxRatePerSec"]) for v in report["violations"]] == [("general", 4)]
    assert sum(t["generalFlashes"] for t in report["timeline"]) == len(fail_job.analysis.qualifying_general)
    assert report["heatmap"]["w"] == 256 and report["heatmap"]["h"] == 64
    assert report["culprits"] == [] and report["fixes"] == []


def test_capture_block_filters_to_dict_to_schema_keys(fail_job: AnalysedJob) -> None:
    capture = report_for(fail_job)["capture"]
    assert set(capture) == set(capture_keys())
    # The wall-clock note changes on every run, so it never reaches the report.
    assert capture["notes"] == ["synthetic frames"]


def test_capture_block_prefers_to_report_dict(fail_job: AnalysedJob) -> None:
    report = report_for(fail_job, FakeCaptureMetaWithReportDict(frame_count=fail_job.analysis.frame_count))
    validate_report(report)
    assert report["capture"]["notes"] == ["from to_report_dict"]


# --------------------------------------------------------------------------------------- ADR-008 escalation
def test_short_capture_is_inconclusive(short_pass_job: AnalysedJob) -> None:
    report = report_for(short_pass_job)
    validate_report(report)
    assert report["verdict"] == "INCONCLUSIVE"
    assert "only 60 frames" in report["reason"] and "120" in report["reason"]


def test_engine_fail_is_fail_under_any_strategy(fail_job: AnalysedJob) -> None:
    report = report_for(fail_job, NONDETERMINISTIC)
    validate_report(report)
    assert report["verdict"] == "FAIL"
    assert report["reason"].startswith(fail_job.analysis.reason())
    assert "not deterministic" in report["reason"]


def test_engine_pass_under_deterministic_capture_is_pass(pass_job: AnalysedJob) -> None:
    report = report_for(pass_job)
    validate_report(report)
    assert report["verdict"] == "PASS"
    assert report["reason"] == PASS_REASON


def test_engine_pass_under_nondeterministic_capture_is_inconclusive(pass_job: AnalysedJob) -> None:
    report = report_for(pass_job, NONDETERMINISTIC)
    validate_report(report)
    assert report["verdict"] == "INCONCLUSIVE"
    assert report["reason"].startswith(PASS_REASON)
    assert NON_DETERMINISTIC_NOTE in report["reason"]


def test_short_capture_that_fails_is_still_fail(tmp_path: Path) -> None:
    # 8 Hz for one second: the engine sees more than 3 flashes even in a capture shorter than 2 s.
    job = analysed_job(tmp_path, square_wave(8), FPS)
    report = report_for(job)
    assert job.analysis.verdict == "FAIL"
    assert report["verdict"] == "FAIL"


def test_capture_error_is_inconclusive_and_schema_valid(tmp_path: Path) -> None:
    settings = AuditSettings(duration_s=2, viewport=(1024, 768))
    report = build_report("file:///x.html", settings, None, None, tmp_path,
                          capture_error="TimeoutError: page did not load", captured_at="2026-10-24T12:00:00Z")
    validate_report(report)
    assert report["verdict"] == "INCONCLUSIVE"
    assert "capture failed" in report["reason"]
    assert report["capture"]["strategy"] == "none" and report["capture"]["frameCount"] == 0
    assert report["evidenceFrames"] == [] and report["heatmap"]["w"] == 0


@pytest.mark.parametrize("meta", [FakeCaptureMeta(), NONDETERMINISTIC])
def test_reason_never_claims_conformance(meta: FakeCaptureMeta, fail_job: AnalysedJob,
                                         pass_job: AnalysedJob, short_pass_job: AnalysedJob) -> None:
    for job in (fail_job, pass_job, short_pass_job):
        assert "compliant" not in report_for(job, meta)["reason"].lower()


# --------------------------------------------------------------------------------------- evidence
def test_evidence_frames_are_copied_and_labelled(fail_job: AnalysedJob) -> None:
    frames = report_for(fail_job)["evidenceFrames"]
    assert [f["index"] for f in frames] == [0, 14, 15]
    assert [f["label"] for f in frames] == [
        "frame 0 (t = 0.000 s) - baseline",
        "frame 14 (t = 0.233 s) - just before",
        "frame 15 (t = 0.250 s) - first qualifying general flash",
    ]
    for frame in frames:
        assert frame["pngPath"] == f"evidence/{frame['index']:06d}.png"
        copied = fail_job.job_dir / frame["pngPath"]
        assert copied.read_bytes() == (fail_job.job_dir / "frames" / f"{frame['index']:06d}.png").read_bytes()


def test_export_outside_the_job_copies_evidence_next_to_the_report(fail_job: AnalysedJob, tmp_path: Path) -> None:
    report = report_for(fail_job)
    out = tmp_path / "elsewhere" / "my-report.json"
    (tmp_path / "elsewhere" / "my-report-evidence").mkdir(parents=True)
    (tmp_path / "elsewhere" / "my-report-evidence" / "999999.png").write_bytes(b"stale")
    exported = export_report(report, fail_job.job_dir, out)
    on_disk = json.loads(out.read_text(encoding="utf-8"))
    assert on_disk == exported
    for frame in on_disk["evidenceFrames"]:
        assert frame["pngPath"].startswith("my-report-evidence/")
        assert (out.parent / frame["pngPath"]).read_bytes() == (fail_job.job_dir / "evidence" / Path(frame["pngPath"]).name).read_bytes()
    assert not (out.parent / "my-report-evidence" / "999999.png").exists()
    assert report["evidenceFrames"][0]["pngPath"] == "evidence/000000.png"  # the job's report is untouched


def test_export_inside_the_job_keeps_paths(fail_job: AnalysedJob) -> None:
    report = report_for(fail_job)
    exported = export_report(report, fail_job.job_dir, fail_job.job_dir / "copy.json")
    assert exported["evidenceFrames"] == report["evidenceFrames"]


# --------------------------------------------------------------------------------------- limitations + assumptions
def spec_section_10_bullets() -> list[str]:
    """The bullet list of SPEC.md section 10, wrapped lines joined with one space."""
    text = SPEC_PATH.read_text(encoding="utf-8")
    section = text.split("## 10.", 1)[1].split("\n## ", 1)[0]
    bullets: list[str] = []
    for line in section.splitlines():
        if line.startswith("* "):
            bullets.append(line[2:].strip())
        elif line.startswith("  ") and bullets and line.strip():
            bullets[-1] += " " + line.strip()
    return bullets


def test_spec_limitations_match_spec_section_10() -> None:
    assert list(SPEC_LIMITATIONS) == spec_section_10_bullets()


def test_capture_specific_limitations(fail_job: AnalysedJob) -> None:
    deterministic = report_for(fail_job)["limitations"]
    assert deterministic[: len(SPEC_LIMITATIONS)] == list(SPEC_LIMITATIONS)
    assert any("crypto.getRandomValues" in item for item in deterministic)
    assert any("faster than 30 per second" in item for item in deterministic)
    realtime = report_for(fail_job, FakeCaptureMeta(strategy="realtime", deterministic=False,
                                                    uncontrollable=["animated GIF: anim.gif"]))["limitations"]
    assert any("strategy C" in item for item in realtime)
    assert any("anim.gif" in item for item in realtime)
    assert not any("crypto.getRandomValues" in item for item in realtime)


def test_assumptions_for_reference_viewport(fail_job: AnalysedJob) -> None:
    assumptions = report_for(fail_job, assumptions=["an extra assumption"])["assumptions"]
    assert not any("scaled" in a for a in assumptions)
    assert any("WCAG 2.2 working definition" in a for a in assumptions)
    assert any("R < 16" in a for a in assumptions)
    assert assumptions[-1] == "an extra assumption"


def test_assumptions_for_other_viewports() -> None:
    wide = standard_assumptions((1366, 768), window_px(1366), "wcag22")
    w, h = window_px(1366)
    assert any(f"1366 px wide" in a and f"{w}x{h} px" in a for a in wide)
    phone = standard_assumptions((375, 812), window_px(375), "wcag21-legacy")
    assert any("last 3 px column(s) and 0 px row(s)" in a for a in phone)
    assert any("pre-2022 rule" in a for a in phone)


# --------------------------------------------------------------------------------------- output
def test_fingerprint_ignores_captured_at_only(fail_job: AnalysedJob) -> None:
    first = report_for(fail_job, captured_at="2026-10-24T12:00:00Z")
    second = report_for(fail_job, captured_at="2027-01-01T00:00:00Z")
    assert first != second
    assert report_fingerprint(first) == report_fingerprint(second)
    changed = dict(first, reason=first["reason"] + " (edited)")
    assert report_fingerprint(changed) != report_fingerprint(first)


def test_same_inputs_give_identical_json(fail_job: AnalysedJob) -> None:
    assert report_json_text(report_for(fail_job)) == report_json_text(report_for(fail_job))


def test_validate_report_names_the_bad_field(fail_job: AnalysedJob) -> None:
    report = report_for(fail_job)
    broken = json.loads(json.dumps(report))
    broken["settings"]["fps"] = 0
    with pytest.raises(ValidationError, match=r"report\.settings\.fps"):
        validate_report(broken)
    broken = json.loads(json.dumps(report))
    broken["evidenceFrames"][1]["index"] = -1
    with pytest.raises(ValidationError, match=r"report\.evidenceFrames\[1\]\.index"):
        validate_report(broken)
    with pytest.raises(ValidationError):
        validate_report({k: v for k, v in report.items() if k != "disclaimer"})


def test_write_report_format(fail_job: AnalysedJob, tmp_path: Path) -> None:
    report = report_for(fail_job)
    report["url"] = "file:///pages/caf\u00e9.html"
    path = write_report(report, tmp_path / "nested" / "report.json")
    text = path.read_text(encoding="utf-8")
    assert text.endswith("}\n")
    assert "caf\u00e9" in text  # ensure_ascii=False
    assert text == json.dumps(json.loads(text), sort_keys=True, indent=2, ensure_ascii=False) + "\n"


# --------------------------------------------------------------------------------------- attribution and fix notes
def tile(rank: int, overlap: float) -> dict[str, object]:
    """A culprit in report.schema.json's shape: a plain div with no animation."""
    return {"rank": rank, "selector": f"#t{rank}", "nodeType": "div", "ftId": None,
            "rect": {"x": 40.0 + 104 * rank, "y": 40.0, "w": 100.0, "h": 200.0}, "animation": None,
            "overlap": overlap, "ablationDelta": None, "inMinimalSet": False}


def test_attribution_notes_copy_an_incomplete_inspection(tmp_path: Path) -> None:
    job = analysed_job(tmp_path, square_wave(4), 2 * FPS)
    (job.job_dir / "attribution.json").write_text(json.dumps({
        "inspectionNotes": ["Math.random replaced by a fixed-seed generator (mulberry32, seed 0x2f6e2b1)",
                            "inspection reached 1 of 2 target frame(s)"],
        "notes": ["a key capture.attribute never writes"],
        "limitations": [], "selectorWarnings": ["#a matched 2 elements"], "error": None}), encoding="utf-8")
    limitations = report_for(job)["limitations"]
    assert ("attribution: inspection reached 1 of 2 target frame(s), so violations whose frame was not reached "
            "have no culprits") in limitations
    assert "attribution: selector #a matched 2 elements" in limitations
    # The routine set-up notes are the capture's own (already in its block); the dead "notes" key is not read.
    assert not any("Math.random replaced" in item or "never writes" in item for item in limitations)


def test_attribution_file_name_is_the_one_capture_attribute_writes() -> None:
    from capture.attribute import ATTRIBUTION_JSON_NAME
    from cli.report import ATTRIBUTION_NOTES_NAME

    assert ATTRIBUTION_NOTES_NAME == ATTRIBUTION_JSON_NAME


def test_report_says_why_its_culprits_got_no_fixes(fail_job: AnalysedJob) -> None:
    from cli.html_report import render_html
    from cli.main import fix_listing
    from cli.report import FIX_NOTE_PREFIX

    report = report_for(fail_job, culprits=[tile(rank, 0.25) for rank in range(1, 5)], fixes=[])
    report["settings"]["attribute"] = "bbox"  # the synthetic job's settings say "none"
    validate_report(report)
    notes = [item for item in report["limitations"] if item.startswith(FIX_NOTE_PREFIX)]
    assert len(notes) == 1 and "IoU of at least 0.5; the best overlap here is 0.25" in notes[0]
    reason = notes[0][len(FIX_NOTE_PREFIX):]
    assert fix_listing(report) == [f"This report has no fixes: {reason}"]
    assert "No fixes in this report: no culprit qualified for a fix." in render_html(report, base_dir=fail_job.job_dir)
    # A culprit that qualifies, or no culprits at all: no such note.
    assert not any(item.startswith(FIX_NOTE_PREFIX) for item in report_for(fail_job, culprits=[tile(1, 0.9)])["limitations"])
    assert fix_listing(report_for(fail_job)) == [
        "This report has no fixes: the audit ran without attribution (use --attribute bbox or ablate)."]
