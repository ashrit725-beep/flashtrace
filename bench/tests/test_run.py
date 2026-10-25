"""Tests for the benchmark runner: scoring (bench/score.py), the Markdown writer (bench/results_md.py)
and one end-to-end run (bench/run.py) on four freshly generated pages in a temporary folder."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from bench import run, score
from bench.results_md import REQUIRED_HEADINGS, attribution_section, mismatch_rows, render_results_md, title
from bench.run import compare_runs, load_ready_truth, row_for, run_bench
from cli.jobs import AuditSettings
from cli.report import capture_block

REPO_ROOT = Path(__file__).resolve().parents[2]


def make_row(page_id: str, expected: str, got: str, *, boundary: tuple[str, ...] = (), mechanism: str = "css",
             types: tuple[str, ...] = (), got_types: tuple[str, ...] = (), advisory: bool = False,
             got_advisory: bool = False, culprits: list[dict[str, Any]] | None = None,
             capture_s: float = 2.0, analysis_s: float = 1.0) -> dict[str, Any]:
    """One hand-made result row in the shape bench/run.py produces."""
    return {
        "id": page_id, "mechanism": mechanism, "rateHz": 4, "areaFraction": 0.5, "areaFractionActual": 0.5,
        "deltaLActual": 0.5, "red": "red" in types, "elementCount": 1,
        "expected": {"verdict": expected, "types": list(types), "advisory": advisory,
                     "boundary": bool(boundary), "boundaryReasons": list(boundary)},
        "truthCulprits": ["#flash-1", "#flash-2"],
        "verdict": got, "violationTypes": list(got_types), "advisory": got_advisory,
        "culprits": culprits or [], "captureWallS": capture_s, "analysisWallS": analysis_s,
        "reason": f"reason for {page_id}",
    }


RATE_REASON = "rate 3 Hz is within 0.15 Hz of the 3 flashes/s limit"
AREA_REASON = "area fraction 0.25 is within 0.0125 of the 25% limit"

ROWS = [
    make_row("p1", "FAIL", "FAIL", types=("general",), got_types=("general",)),
    make_row("p2", "FAIL", "FAIL", types=("general", "red"), got_types=("red",), mechanism="waapi"),
    make_row("p3", "PASS", "PASS", advisory=True, got_advisory=True),
    make_row("p4", "PASS", "INCONCLUSIVE", mechanism="video"),
    make_row("p5", "PASS", "ERROR", capture_s=99.0, analysis_s=99.0),
    make_row("p6", "FAIL", "PASS", boundary=(RATE_REASON,)),
    make_row("p7", "PASS", "PASS", boundary=(RATE_REASON, AREA_REASON)),
    make_row("p8", "SKIP", "SKIP", mechanism="video-skipped"),
]


# --------------------------------------------------------------------------------------- score.py
def test_confusion_table_counts_expected_against_got() -> None:
    table = score.confusion_table(ROWS)
    assert table["FAIL"] == {"PASS": 1, "FAIL": 2, "INCONCLUSIVE": 0, "ERROR": 0}
    assert table["PASS"] == {"PASS": 2, "FAIL": 0, "INCONCLUSIVE": 1, "ERROR": 1}


def test_clear_case_accuracy_leaves_out_boundary_skip_and_error() -> None:
    clear = score.clear_case_scores(ROWS)
    assert clear["pages"] == 5  # p1-p5
    assert clear["accuracy"] == {"correct": 3, "total": 4, "accuracy": 0.75, "errors": 1}
    assert clear["confusion"]["PASS"]["ERROR"] == 1


def test_boundary_cases_are_split_by_reason() -> None:
    boundary = score.boundary_scores(ROWS)
    assert boundary["pages"] == 2
    assert boundary["accuracy"]["correct"] == 1 and boundary["accuracy"]["total"] == 2
    by_reason = {item["reason"]: (item["pages"], item["matching"]) for item in boundary["byReason"]}
    assert by_reason[score.boundary_kind(RATE_REASON)] == (2, 1)
    assert by_reason[score.boundary_kind(AREA_REASON)] == (1, 1)
    assert score.boundary_kind("something new") == "something new"


def test_mechanism_accuracy_on_clear_cases() -> None:
    mechanisms = {m["mechanism"]: (m["correct"], m["total"]) for m in score.mechanism_scores(ROWS)}
    assert mechanisms == {"css": (2, 2), "video": (0, 1), "waapi": (1, 1)}


def test_type_agreement_on_pages_that_both_fail() -> None:
    types = score.type_agreement(ROWS)
    assert (types["agree"], types["total"], types["disagreements"]) == (1, 2, ["p2"])


def test_advisory_agreement_needs_a_long_capture() -> None:
    # The advisory span must last longer than 5 s and cannot start before a full one-second window
    # holds two flashes, so captures up to 5 s + 1 s are not measured (the engine cannot be expected to
    # raise the advisory there; at 5.5 s it never does for 2-3 Hz pages).
    assert score.ADVISORY_MIN_CAPTURE_SECONDS == 6.0
    for duration in (1.5, 5.0, 5.5, 6.0):
        short = score.advisory_agreement(ROWS, duration)
        assert short["measured"] is False and short["total"] == 0, duration
        assert "not measured" in short["note"] and "longer than 6 s" in short["note"]
    for duration in (6.5, 10.0):
        long = score.advisory_agreement(ROWS, duration)
        # Clear, expected-PASS, not ERROR: p3 (advisory expected and found) and p4 (none expected, none found).
        assert long["measured"] is True and (long["agree"], long["total"]) == (2, 2)


def attributed_row(page_id: str, chosen: tuple[str, ...], top: str = "#flash-1", got: str = "FAIL",
                   rate_hz: float = 4) -> dict[str, Any]:
    """A FAIL page with three truth elements of 900, 1 800 and 600 cells (3 300 in one window)."""
    culprits = [{"selector": top, "inMinimalSet": top in chosen}]
    culprits += [{"selector": s, "inMinimalSet": True} for s in chosen if s != top]
    row = make_row(page_id, "FAIL", got, culprits=culprits)
    row["rateHz"] = rate_hz
    row["truthCulprits"] = ["#flash-1", "#flash-2", "#flash-3"]
    # Cell-aligned rectangles (ADR-014): 30x30, 60x30 and 20x30 cells.
    row["truthElements"] = [{"selector": "#flash-1", "rect": {"x": 0, "y": 0, "w": 120, "h": 120}},
                            {"selector": "#flash-2", "rect": {"x": 124, "y": 0, "w": 240, "h": 120}},
                            {"selector": "#flash-3", "rect": {"x": 368, "y": 0, "w": 80, "h": 120}}]
    return row


def test_attribution_scoring() -> None:
    assert score.attribution_scores(ROWS, "none") == {"measured": False, "note": "not measured (attribution: none)"}
    rows = [
        attributed_row("valid", ("#flash-1", "#flash-2")),            # leaves 600 cells; each member is needed
        attributed_row("not-minimal", ("#flash-1", "#flash-2", "#flash-3")),  # #flash-3 is not needed
        attributed_row("decoy", ("#decoy", "#flash-2"), top="#decoy"),  # top-1 wrong, set holds a non-culprit
        attributed_row("too-small", ("#flash-2",)),                    # leaves 1 500 cells > 1 360: still fails
        attributed_row("missed", (), got="PASS"),                      # not scored: FlashTrace did not FAIL
    ]
    result = score.attribution_scores(rows, "ablate")
    assert result["mode"] == "ablate" and result["pages"] == 4
    assert result["top1"] == {"agree": 3, "total": 4, "accuracy": 0.75}
    assert result["minimalSet"] == {"agree": 1, "total": 4, "accuracy": 0.25}
    bbox = score.attribution_scores(rows, "bbox")
    assert bbox["top1"]["agree"] == 3 and bbox["minimalSet"]["measured"] is False


def test_minimal_set_is_judged_by_truth_geometry_not_set_equality() -> None:
    # Two different minimal sets explain the same page: {#flash-1, #flash-2} and {#flash-2, #flash-3}.
    assert score.minimal_set_correct(attributed_row("a", ("#flash-1", "#flash-2")))
    assert score.minimal_set_correct(attributed_row("b", ("#flash-2", "#flash-3"), top="#flash-2"))
    assert not score.minimal_set_correct(attributed_row("c", ()))  # nothing marked
    # A FAIL page without any culprit still counts in the top-1 total, as wrong.
    empty = make_row("d", "FAIL", "FAIL")
    assert score.attribution_pages([empty]) == [empty] and not score.top1_correct(empty)


def test_ablation_sample_scores_report_size_and_settings() -> None:
    assert score.ablation_sample_scores(None)["measured"] is False
    sample_rows = [attributed_row("s1", ("#flash-1", "#flash-2")), attributed_row("s2", (), got="PASS")]
    sample_rows[0].update(attributionRecaptures=5, auditWallS=12.0)
    result = score.ablation_sample_scores({"requested": 3, "duration": 3.0, "rows": sample_rows})
    assert (result["requested"], result["duration"], result["sampled"], result["pages"], result["notFail"]) == (3, 3.0, 2, 1, 1)
    assert result["top1"]["agree"] == 1 and result["minimalSet"] == {"agree": 1, "total": 1, "accuracy": 1.0}
    assert result["recaptures"]["mean"] == 5.0 and result["auditWall"]["max"] == 12.0
    assert result["pageIds"] == ["s1", "s2"]
    text = "\n".join(attribution_section({"attribution": score.attribution_scores(sample_rows, "bbox"),
                                          "ablationSample": result}))
    assert "first 3 pages that FAILed in the main pass" in text and "at 3 s per capture" in text
    assert "Sampled 2; scored 1" in text and "Minimal culprit set correct: 1/1 (100.0%)" in text
    assert "not compared with the truth list for equality" in text


def test_ablation_sample_takes_the_first_fail_pages(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    seen: list[dict[str, Any]] = []

    class FakePool:
        def __init__(self, processes: int) -> None:
            pass

        def __enter__(self) -> FakePool:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

        def imap(self, func: Any, tasks: list[dict[str, Any]], chunksize: int = 1) -> Any:
            seen.extend(tasks)
            return (dict(outcome(t["id"], verdict="FAIL"), culprits=[]) for t in tasks)

    monkeypatch.setattr(run.multiprocessing, "get_context", lambda method: type("C", (), {"Pool": lambda self, processes: FakePool(processes)})())
    config = dataclasses.replace(bench_config(tmp_path, tmp_path / "R.md", "x"), ablate_sample=2, ablate_duration=3.0)
    audited = [{"id": f"p{i}", "file": f"pages/p{i}.html", "mechanism": "css", "rateHz": 4, "areaFraction": 0.6,
                "areaFractionActual": 0.6, "deltaLActual": 0.5, "red": False, "elementCount": 1,
                "expected": {"verdict": "FAIL"}, "culprits": ["#flash-1"], "elements": []} for i in range(4)]
    first = [outcome("p0", verdict="PASS"), outcome("p1", verdict="FAIL"), outcome("p2", verdict="FAIL"),
             outcome("p3", verdict="FAIL")]
    sample = run.ablation_sample(config, audited, first, AuditSettings())
    assert sample is not None and [row["id"] for row in sample["rows"]] == ["p1", "p2"]
    assert all(t["settings"].attribute == "ablate" and t["settings"].duration_s == 3.0 for t in seen)
    assert sample["requested"] == 2 and sample["duration"] == 3.0
    assert run.ablation_sample(bench_config(tmp_path, tmp_path / "R.md", "x"), audited, first, AuditSettings()) is None
    bad = dataclasses.replace(config, ablate_sample=-1, ablate_duration=0.0)
    assert len(bad.problems()) == 2


def test_runtime_stats_skip_errors_and_skips() -> None:
    runtime = score.runtime_scores(ROWS)
    # p5 (ERROR) and p8 (SKIP) are left out: six measured pages, all at 2.0 s capture / 1.0 s analysis.
    assert runtime["capture"] == {"count": 6, "mean": 2.0, "median": 2.0, "min": 2.0, "max": 2.0}
    assert runtime["analysis"]["max"] == 1.0
    assert runtime["failedCaptures"] == 0
    assert score.runtime_stats([]) is None
    stats = score.runtime_stats([1.0, 2.0, 6.0])
    assert stats == {"count": 3, "mean": 3.0, "median": 2.0, "min": 1.0, "max": 6.0}


def failed_capture_row(page_id: str) -> dict[str, Any]:
    """An INCONCLUSIVE row whose capture failed after 0.2 s, as cli/audit.py reports it."""
    row = make_row(page_id, "PASS", "INCONCLUSIVE", capture_s=0.2, analysis_s=0.0)
    row["strategy"] = capture_block(None, "TimeoutError: navigation timed out")["strategy"]
    return row


def test_runtime_stats_leave_out_failed_captures() -> None:
    worked = make_row("ok", "PASS", "PASS", capture_s=40.0, analysis_s=3.0)
    worked["strategy"] = "waapi_step"
    runtime = score.runtime_scores([worked, failed_capture_row("broken")])
    # Only the capture that recorded the animation counts; the 0.2 s time-to-failure does not.
    assert runtime["capture"] == {"count": 1, "mean": 40.0, "median": 40.0, "min": 40.0, "max": 40.0}
    assert runtime["analysis"] == {"count": 1, "mean": 3.0, "median": 3.0, "min": 3.0, "max": 3.0}
    assert runtime["failedCaptures"] == 1
    # The scorer's name for a failed capture is the one cli/report.py writes.
    assert capture_block(None, "x")["strategy"] == score.FAILED_CAPTURE_STRATEGY


# --------------------------------------------------------------------------------------- results_md.py
def fake_results(n: int = 8, duration: float = 1.5, command: str = "flashtrace bench --n 8") -> dict[str, Any]:
    return {
        "date": "2026-10-24 12:00:00 UTC", "commit": "abc1234-dirty",
        "commandLine": command, "resultsJson": "docs/RESULTS.json", "resultsJsonSha256": "0" * 64,
        "resultsMd": "docs/RESULTS.md",
        "machine": {"platform": "test-platform", "python": "3.14.0", "chromium": "1.2.3", "cpus": 8},
        "settings": {"n": n, "seed": 1, "duration": duration, "fps": 60, "viewport": [1024, 768],
                     "strategy": "auto", "redRule": "wcag22", "attribute": "none", "workers": 4,
                     "poolProcesses": 3, "determinismSample": 2, "truth": "bench/truth.json", "pages": "bench/pages"},
        "counts": {"pages": 8, "audited": 7, "skipped": 1, "errors": 1},
        "scores": score.score_all(ROWS, duration, "none"),
        "determinism": {"total": 2, "compared": 2, "notCompared": 0, "identical": 2, "frameHashesIdentical": 2,
                        "fingerprintsIdentical": 2, "pages": []},
        "rows": ROWS,
    }


def test_results_markdown_has_every_section() -> None:
    text = render_results_md(fake_results())
    for heading in REQUIRED_HEADINGS:
        assert f"\n{heading}\n" in text
    assert text.startswith("# RESULTS — partial run (n=8, duration=1.5 s)\n")
    assert "Determinism: 2/2 compared pages identical on a second run" in text
    assert "Not compared: 0 of 2 sampled pages" in text
    assert "**Accuracy: 3/4 (75.0%)**" in text
    assert "Not measured (attribution: none)" in text
    assert "abc1234-dirty" in text and "`flashtrace bench --n 8`" in text
    # The JSON is named by its repository path and linked by file name (it sits next to this page).
    assert f"| Per-page results | [`docs/RESULTS.json`](RESULTS.json) (sha256 `{'0' * 64}`) |" in text
    assert "Full per-page results: [`docs/RESULTS.json`](RESULTS.json), written by the same run" in text
    assert "| Record warnings | none |" in text
    assert text.rstrip().endswith("Analysis aid, not a compliance certification.")


def test_markdown_states_the_pool_size_actually_used() -> None:
    # 4 workers requested but only 3 processes ran (fewer pages than workers).
    text = render_results_md(fake_results())
    assert "with up to 3 pages audited at the same time (3 worker processes)" in text
    assert "| Worker processes | 3 used (4 requested) |" in text
    assert "4 pages audited" not in text


def test_markdown_determinism_reports_pages_not_compared() -> None:
    results = fake_results()
    results["determinism"] = {"total": 3, "compared": 2, "notCompared": 1, "identical": 2,
                              "frameHashesIdentical": 2, "fingerprintsIdentical": 2, "pages": []}
    text = render_results_md(results)
    assert "Determinism: 2/2 compared pages identical on a second run (frame hashes 2/2" in text
    assert "Not compared: 1 of 3 sampled pages" in text
    results["determinism"] = {"total": 1, "compared": 0, "notCompared": 1, "identical": 0,
                              "frameHashesIdentical": 0, "fingerprintsIdentical": 0, "pages": []}
    text = render_results_md(results)
    assert "Determinism: not measured (no sampled page captured frames on both runs)." in text
    assert "Not compared: 1 of 1 sampled pages" in text


def test_markdown_runtime_counts_left_out_pages() -> None:
    results = fake_results()
    results["scores"]["runtime"]["failedCaptures"] = 2
    text = render_results_md(results)
    assert "Left out: pages whose audit raised an error (1) and pages whose capture failed" in text
    assert "not a capture time (2)." in text


def test_title_is_partial_unless_full_size() -> None:
    assert title(200, 10.0) == "# RESULTS"
    assert "partial run" in title(200, 9.5) and "partial run" in title(199, 10.0)


def test_mismatch_table_lists_only_mismatches() -> None:
    ids = [row[0] for row in mismatch_rows(ROWS)]
    assert ids == ["p2", "p4", "p5", "p6"]  # type mismatch, INCONCLUSIVE, ERROR, boundary miss


# --------------------------------------------------------------------------------------- run.py helpers
def outcome(page_id: str, verdict: str = "PASS", hashes: tuple[str, ...] = ("a", "b"), fp: str = "f") -> dict[str, Any]:
    return {"id": page_id, "verdict": verdict, "frameHashes": list(hashes), "fingerprint": fp}


def test_compare_runs() -> None:
    first = [outcome("1"), outcome("2"), outcome("3"), outcome("4", verdict="ERROR")]
    second = [outcome("1"), outcome("2", hashes=("a", "c")), outcome("3", fp="g"), outcome("4", verdict="ERROR")]
    result = compare_runs(first, second)
    assert (result["total"], result["identical"]) == (4, 1)
    assert (result["compared"], result["notCompared"]) == (3, 1)
    assert (result["frameHashesIdentical"], result["fingerprintsIdentical"]) == (2, 2)


def test_two_failed_captures_are_not_compared() -> None:
    # A failed capture is INCONCLUSIVE with no frame hashes and the same error text on both runs, so
    # its hashes and fingerprint "match"; that must not count as a deterministic page.
    failed = outcome("dead", verdict="INCONCLUSIVE", hashes=(), fp="same-error")
    one_frame = outcome("short", verdict="INCONCLUSIVE", hashes=("a",))
    result = compare_runs([outcome("ok"), failed, one_frame, outcome("half")],
                          [outcome("ok"), dict(failed), dict(one_frame), outcome("half", verdict="INCONCLUSIVE",
                                                                                 hashes=())])
    assert (result["total"], result["compared"], result["notCompared"]) == (4, 1, 3)
    assert (result["identical"], result["frameHashesIdentical"], result["fingerprintsIdentical"]) == (1, 1, 1)
    assert [p["compared"] for p in result["pages"]] == [True, False, False, False]


def test_row_for_skipped_page_and_audited_page() -> None:
    entry = {"id": "bench-0009", "mechanism": "video-skipped", "rateHz": 4, "areaFraction": 0.5,
             "areaFractionActual": 0.5, "deltaLActual": 0.5, "red": False, "elementCount": 1,
             "expected": {"verdict": "SKIP", "types": [], "advisory": False, "boundary": False,
                          "boundaryReasons": [], "skipReason": "ffmpeg not found"}, "culprits": []}
    assert row_for(entry, None)["verdict"] == "SKIP"
    audited = row_for(entry, outcome("bench-0009"))
    assert audited["verdict"] == "PASS" and "frameHashes" not in audited


def bench_config(tmp_path: Path, out: Path, command: str) -> run.BenchConfig:
    return run.BenchConfig(n=8, seed=1, duration_s=1.5, fps=60, workers=4, attribute="none",
                           determinism_sample=2, out=out, regenerate=False, pages=tmp_path / "pages",
                           truth=tmp_path / "truth.json", work_dir=tmp_path / "work", command_line=command,
                           quiet=True, keep_frames=False)


def test_results_json_sits_next_to_the_markdown(tmp_path: Path) -> None:
    assert run.results_json_path(Path("docs/RESULTS.md")) == Path("docs/RESULTS.json")
    assert run.results_json_path(Path("out/partial")) == Path("out/partial.json")
    # An --out ending in .json must not make the JSON overwrite the Markdown.
    assert run.results_json_path(Path("out/r.json")) == Path("out/r.rows.json")


def test_summary_names_files_by_repository_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # A committed RESULTS.md must not carry one machine's folders (the M3 record cited a scratch folder).
    assert run.repo_path(REPO_ROOT / "docs" / "RESULTS.json") == "docs/RESULTS.json"
    assert run.repo_path(tmp_path / "RESULTS.json") == str((tmp_path / "RESULTS.json").resolve())
    monkeypatch.setattr(run, "machine_info", lambda: {})  # no Chromium launch in a non-browser test
    config = bench_config(tmp_path, REPO_ROOT / "docs" / "RESULTS.md", "flashtrace bench")
    results = run.build_results(config, AuditSettings(), [], compare_runs([], []), 1)
    assert (results["resultsMd"], results["resultsJson"]) == ("docs/RESULTS.md", "docs/RESULTS.json")


def test_per_page_rows_name_the_job_folder_by_repository_path() -> None:
    report = {"verdict": "PASS", "reason": "r", "violations": [], "advisory": [], "culprits": [],
              "capture": {"strategy": "waapi_step", "deterministic": True, "hash": "h", "frameHashes": ["a", "b"]}}
    job_dir = REPO_ROOT / ".flashtrace" / "bench" / "run1" / "jobs" / "job-1"
    row = run.outcome_from_report("p1", report, job_dir, {})
    assert row["jobDir"] == ".flashtrace/bench/run1/jobs/job-1"


def test_record_warnings_name_out_outside_repo_and_uncommitted_changes(monkeypatch: pytest.MonkeyPatch,
                                                                         tmp_path: Path) -> None:
    official = REPO_ROOT / "docs" / "RESULTS.md"
    monkeypatch.setattr(run, "uncommitted_paths", lambda ignore=(): [])
    assert run.record_warnings(official) == []
    outside = run.record_warnings(tmp_path / "RESULTS.md")
    assert len(outside) == 1 and "outside the repository" in outside[0]
    monkeypatch.setattr(run, "uncommitted_paths", lambda ignore=(): ["engine/analyze.py", "bench/run.py"])
    dirty = run.record_warnings(official)
    assert len(dirty) == 1 and "2 uncommitted change(s) (first: engine/analyze.py)" in dirty[0]
    monkeypatch.setattr(run, "uncommitted_paths", lambda ignore=(): None)
    assert "git cannot list uncommitted changes" in run.record_warnings(official)[0]
    lines = run.summary_lines(dict(fake_results(), recordWarnings=dirty))
    assert lines[-1] == f"warning: {dirty[0]}"


def test_uncommitted_paths_leave_out_this_runs_own_outputs(monkeypatch: pytest.MonkeyPatch) -> None:
    # A summary left over from an earlier run changes no measurement; an edited engine file does.
    status = " M docs/RESULTS.md\n?? docs/RESULTS.json\n M engine/analyze.py\n"
    monkeypatch.setattr(run, "git", lambda *args: "abc1234\n" if args[0] == "rev-parse" else status)
    outputs = (REPO_ROOT / "docs" / "RESULTS.md", REPO_ROOT / "docs" / "RESULTS.json")
    assert run.uncommitted_paths(ignore=outputs) == ["engine/analyze.py"]
    assert run.commit_hash(ignore=outputs) == "abc1234-dirty"
    status = " M docs/RESULTS.md\n?? docs/RESULTS.json\n"
    assert run.uncommitted_paths(ignore=outputs) == [] and run.commit_hash(ignore=outputs) == "abc1234"
    assert run.commit_hash() == "abc1234-dirty"


def test_a_later_run_does_not_replace_the_json_an_earlier_summary_cites(tmp_path: Path) -> None:
    official_md = tmp_path / "docs" / "RESULTS.md"
    official = bench_config(tmp_path, official_md, "flashtrace bench")
    official_results = dict(fake_results(command="flashtrace bench"),
                            resultsJson=str(run.results_json_path(official_md)), resultsMd=str(official_md))
    digest = run.write_outputs(official, official_results)
    # A partial run sharing the same work directory but writing its summary elsewhere.
    partial_md = tmp_path / "scratch" / "partial.md"
    partial = bench_config(tmp_path, partial_md, "flashtrace bench --n 3")
    partial_results = dict(fake_results(command="flashtrace bench --n 3"),
                           resultsJson=str(run.results_json_path(partial_md)), resultsMd=str(partial_md))
    run.write_outputs(partial, partial_results)
    cited = tmp_path / "docs" / "RESULTS.json"
    assert json.loads(cited.read_text(encoding="utf-8"))["commandLine"] == "flashtrace bench"
    assert hashlib.sha256(cited.read_bytes()).hexdigest() == digest
    text = official_md.read_text(encoding="utf-8")
    assert f"[`{cited}`](RESULTS.json) (sha256 `{digest}`)" in text and "results.json" not in text
    # The work-directory copy is only a convenience and now holds the later run.
    work_copy = json.loads((tmp_path / "work" / "results.json").read_text(encoding="utf-8"))
    assert work_copy["commandLine"] == "flashtrace bench --n 3"


def test_pool_never_has_more_processes_than_pages(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    assert run.pool_size(4, 3) == 3 and run.pool_size(4, 200) == 4
    started: list[int] = []

    class FakePool:
        def __init__(self, processes: int) -> None:
            started.append(processes)

        def __enter__(self) -> FakePool:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

        def imap(self, func: Any, tasks: list[dict[str, Any]], chunksize: int = 1) -> Any:
            return map(func, tasks)

    class FakeContext:
        def Pool(self, processes: int) -> FakePool:  # noqa: N802 - mirrors multiprocessing's name
            return FakePool(processes)

    monkeypatch.setattr(run.multiprocessing, "get_context", lambda method: FakeContext())
    monkeypatch.setattr(run, "audit_page", lambda task: outcome(task["id"]))
    config = bench_config(tmp_path, tmp_path / "RESULTS.md", "flashtrace bench --n 3 --workers 4")
    audited = [{"id": f"p{i}", "file": f"pages/p{i}.html"} for i in range(3)]
    first, second = run.audit_all(config, audited, AuditSettings(), run.pool_size(config.workers, len(audited)))
    assert started == [3] and [o["id"] for o in first] == ["p0", "p1", "p2"] and len(second) == 2


def test_load_ready_truth_requires_seed_count_and_files(tmp_path: Path) -> None:
    (tmp_path / "pages").mkdir()
    (tmp_path / "pages" / "a.html").write_text("x", encoding="utf-8")
    truth = {"seed": 1, "pages": [{"id": "a", "file": "pages/a.html", "videos": []}]}
    (tmp_path / "truth.json").write_text(json.dumps(truth), encoding="utf-8")
    assert load_ready_truth(tmp_path / "truth.json", 1, 1) == truth
    assert load_ready_truth(tmp_path / "truth.json", 2, 1) is None  # too few pages
    assert load_ready_truth(tmp_path / "truth.json", 1, 2) is None  # other seed
    (tmp_path / "pages" / "a.html").unlink()
    assert load_ready_truth(tmp_path / "truth.json", 1, 1) is None  # page file missing


# --------------------------------------------------------------------------------------- end to end
def first_pages_copy(tmp_path: Path, n: int) -> Path:
    """A truth file with the first ``n`` pages in ``tmp_path``.

    Copied from the repository's generated bench/pages when they are there (so this test does not
    depend on ffmpeg); otherwise run_bench generates them into ``tmp_path`` itself.
    """
    truth_path = tmp_path / "truth.json"
    source = load_ready_truth(REPO_ROOT / "bench" / "truth.json", n, 1)
    if source is None:
        return truth_path
    entries = source["pages"][:n]
    for entry in entries:
        for name in [entry["file"], *entry["videos"]]:
            target = tmp_path / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(REPO_ROOT / "bench" / name, target)
    truth_path.write_text(json.dumps(dict(source, n=n, pages=entries)), encoding="utf-8")
    return truth_path


@pytest.mark.browser
def test_partial_bench_end_to_end(tmp_path: Path) -> None:
    official = REPO_ROOT / "docs" / "RESULTS.md"
    before = official.read_bytes() if official.exists() else None
    out = tmp_path / "RESULTS.md"
    truth = first_pages_copy(tmp_path, 4)
    results = run_bench(n=4, seed=1, duration=1.5, workers=2, determinism_sample=2, out=out,
                        pages=tmp_path / "pages", truth=truth, work_dir=tmp_path / "work",
                        command_line="flashtrace bench --n 4 --duration 1.5 --workers 2 --determinism-sample 2",
                        quiet=True)
    text = out.read_text(encoding="utf-8")
    assert "partial run (n=4, duration=1.5 s)" in text
    assert "Determinism: " in text and results["determinism"]["total"] == 2
    # The per-page JSON sits next to the Markdown, which names it and its sha256.
    json_path = tmp_path / "RESULTS.json"
    assert hashlib.sha256(json_path.read_bytes()).hexdigest() == results["resultsJsonSha256"]
    assert f"[`{json_path.resolve()}`](RESULTS.json) (sha256 `{results['resultsJsonSha256']}`)" in text
    # tmp_path is outside the repository: the summary says it cannot be the committed record.
    assert any("outside the repository" in warning for warning in results["recordWarnings"])
    assert "| Record warnings | --out " in text
    assert results["settings"]["poolProcesses"] == 2
    saved = json.loads(json_path.read_text(encoding="utf-8"))
    assert saved == json.loads((tmp_path / "work" / "results.json").read_text(encoding="utf-8"))
    assert [row["id"] for row in saved["rows"]] == ["bench-0001", "bench-0002", "bench-0003", "bench-0004"]
    assert all(row["verdict"] in ("PASS", "FAIL", "INCONCLUSIVE", "ERROR", "SKIP") for row in saved["rows"])
    assert results["counts"]["audited"] + results["counts"]["skipped"] == 4
    assert (official.read_bytes() if official.exists() else None) == before
