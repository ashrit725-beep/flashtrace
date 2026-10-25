"""``flashtrace bench``: audit the synthetic benchmark pages and score them against the truth file.

Steps:
1. Make sure the pages and bench/truth.json exist (generate them with bench.generate otherwise).
2. Audit every page that is not SKIP with cli.audit.run_audit, the same code path as
   ``flashtrace audit``, in a pool of worker processes started with the "spawn" method.  Results are
   kept in page order; a page whose audit raises becomes an ERROR outcome and the run goes on.
3. Audit the first ``determinism_sample`` pages a second time and compare frame hashes and report
   fingerprints.
4. Score (bench/score.py), then write the per-page JSON next to the Markdown summary (``--out`` with a
   .json suffix: docs/RESULTS.json for the official run), a convenience copy at
   <work_dir>/results.json, and the Markdown summary itself (bench/results_md.py; docs/RESULTS.md by
   default), which names the JSON file and its sha256.
"""

from __future__ import annotations

import hashlib
import json
import multiprocessing
import os
import platform
import shlex
import shutil
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from multiprocessing.pool import Pool
from pathlib import Path
from typing import Any

from bench import generate as generator
from bench.results_md import ratio_text, write_results_md
from bench.score import score_all
from cli.audit import AuditInputError, find_attributor, run_audit
from cli.jobs import FRAMES_DIR_NAME, AuditSettings
from cli.report import report_fingerprint

REPO_ROOT = Path(__file__).resolve().parents[1]

# Truth fields copied into every result row (the page's parameters, for the mismatch table).
TRUTH_FIELDS: tuple[str, ...] = ("mechanism", "rateHz", "areaFraction", "areaFractionActual",
                                 "deltaLActual", "red", "elementCount")

# The engine needs at least two frames to see any change (SPEC 5.1: "T >= 2"), so a run with fewer
# frame hashes than this captured nothing worth comparing for determinism.
MIN_COMPARED_FRAMES = 2

# Name of the convenience copy of the per-page JSON inside the work directory.  The copy is shared by
# every run that uses the same work directory, so RESULTS.md never cites it (see results_json_path).
WORK_DIR_RESULTS_NAME = "results.json"


@dataclass(frozen=True)
class BenchConfig:
    """Everything one benchmark run was asked to do."""

    n: int
    seed: int
    duration_s: float
    fps: int
    workers: int
    attribute: str
    determinism_sample: int
    out: Path
    regenerate: bool
    pages: Path
    truth: Path
    work_dir: Path
    command_line: str
    quiet: bool
    keep_frames: bool

    def problems(self) -> list[str]:
        found = []
        if self.n < 1:
            found.append(f"--n must be at least 1, got {self.n}")
        if self.workers < 1:
            found.append(f"--workers must be at least 1, got {self.workers}")
        if self.determinism_sample < 0:
            found.append(f"--determinism-sample cannot be negative, got {self.determinism_sample}")
        return found


def first_line(exc: BaseException) -> str:
    text = str(exc).strip()
    return f"{type(exc).__name__}: {text.splitlines()[0]}" if text else type(exc).__name__


def log(config: BenchConfig, text: str) -> None:
    if not config.quiet:
        print(text, file=sys.stderr, flush=True)


# --------------------------------------------------------------------------------------- pages
def load_ready_truth(truth_path: Path, n: int, seed: int) -> dict[str, Any] | None:
    """The truth file if it has this seed, at least n pages, and every needed page file exists."""
    if not truth_path.is_file():
        return None
    truth = json.loads(truth_path.read_text(encoding="utf-8"))
    if truth.get("seed") != seed or len(truth.get("pages", [])) < n:
        return None
    for entry in truth["pages"][:n]:
        if not all((truth_path.parent / name).is_file() for name in [entry["file"], *entry.get("videos", [])]):
            return None
    return truth


def ensure_truth(config: BenchConfig) -> dict[str, Any]:
    """Load the truth file, regenerating pages + truth when asked or when anything is missing.

    Regeneration never shrinks an existing truth file with the same seed: page i is the same for any n
    (ADR-017), so asking for 3 pages keeps all 200 entries of a 200-page truth file.
    """
    truth = None if config.regenerate else load_ready_truth(config.truth, config.n, config.seed)
    if truth is not None:
        return truth
    existing = 0
    if config.truth.is_file():
        old = json.loads(config.truth.read_text(encoding="utf-8"))
        existing = len(old.get("pages", [])) if old.get("seed") == config.seed else 0
    count = max(config.n, existing)
    log(config, f"bench: generating {count} pages (seed {config.seed}) into {config.pages}")
    return generator.generate(n=count, seed=config.seed, out_dir=config.pages, truth_path=config.truth)


# --------------------------------------------------------------------------------------- one page
def audit_page(task: dict[str, Any]) -> dict[str, Any]:
    """Audit one page (runs in a worker process).  Never raises: a failure is an ERROR outcome."""
    measurements: dict[str, float] = {}
    try:
        report, job_dir, _code = run_audit(task["path"], task["settings"], work_dir=task["workDir"],
                                           measurements=measurements)
    except Exception as exc:  # noqa: BLE001 - one page must never abort the run
        return {"id": task["id"], "verdict": "ERROR", "error": first_line(exc)}
    if not task["keepFrames"]:
        # Evidence stills were already copied into evidence/; the full frame set is only needed again
        # for debugging, and 600 PNGs per page add up over 200 pages.
        shutil.rmtree(job_dir / FRAMES_DIR_NAME, ignore_errors=True)
    return outcome_from_report(task["id"], report, job_dir, measurements)


def outcome_from_report(page_id: str, report: dict[str, Any], job_dir: Path,
                        measurements: dict[str, float]) -> dict[str, Any]:
    """The per-page facts the scorer and the determinism check need."""
    capture = report["capture"]
    return {
        "id": page_id,
        "verdict": report["verdict"],
        "reason": report["reason"],
        "violationTypes": sorted({str(v["type"]) for v in report["violations"]}),
        "generalMaxRate": measurements.get("general_max_rate", 0.0),
        "redMaxRate": measurements.get("red_max_rate", 0.0),
        "advisory": bool(report["advisory"]),
        "strategy": capture["strategy"],
        "deterministic": bool(capture["deterministic"]),
        "frameHash": capture["hash"],
        "frameHashes": list(capture["frameHashes"]),
        "fingerprint": report_fingerprint(report),
        "captureWallS": measurements.get("capture_s", 0.0),
        "analysisWallS": measurements.get("analysis_s", 0.0),
        "culprits": [{"selector": c["selector"], "inMinimalSet": bool(c["inMinimalSet"])} for c in report["culprits"]],
        "jobDir": str(job_dir),
    }


def page_tasks(entries: Sequence[dict[str, Any]], truth_dir: Path, settings: AuditSettings,
               work_dir: Path, keep_frames: bool) -> list[dict[str, Any]]:
    return [{"id": e["id"], "path": str(truth_dir / e["file"]), "settings": settings,
             "workDir": str(work_dir), "keepFrames": keep_frames} for e in entries]


def run_tasks(pool: Pool, tasks: list[dict[str, Any]], label: str, config: BenchConfig) -> list[dict[str, Any]]:
    """Audit ``tasks`` in the pool; results come back in task order."""
    outcomes: list[dict[str, Any]] = []
    for done, outcome in enumerate(pool.imap(audit_page, tasks, chunksize=1), start=1):
        outcomes.append(outcome)
        log(config, f"bench {label}: {done}/{len(tasks)} {outcome['id']} {outcome['verdict']}")
    return outcomes


# --------------------------------------------------------------------------------------- comparison
def captured_frames(outcome: dict[str, Any]) -> bool:
    """True when this audit produced an answer from real frames.

    An ERROR outcome is not an answer.  A failed capture is not an ERROR (it becomes an INCONCLUSIVE
    report with no frame hashes), but it recorded nothing, so it is not a capture either.
    """
    return outcome["verdict"] != "ERROR" and len(outcome["frameHashes"]) >= MIN_COMPARED_FRAMES


def compare_runs(first: Sequence[dict[str, Any]], second: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Per page: identical frame-hash lists?  identical report fingerprints?

    A page is *compared* only when both runs captured frames.  Two failed captures would otherwise
    "agree" (two empty hash lists, the same error text), which says nothing about determinism, so such
    pages are counted separately as not compared and are never identical.
    """
    pages = []
    for a, b in zip(first, second):
        compared = captured_frames(a) and captured_frames(b)
        pages.append({
            "id": a["id"],
            "compared": compared,
            "frameHashes": compared and a["frameHashes"] == b["frameHashes"],
            "fingerprint": compared and a["fingerprint"] == b["fingerprint"],
        })
    compared_count = sum(1 for p in pages if p["compared"])
    return {
        "total": len(pages),
        "compared": compared_count,
        "notCompared": len(pages) - compared_count,
        "identical": sum(1 for p in pages if p["frameHashes"] and p["fingerprint"]),
        "frameHashesIdentical": sum(1 for p in pages if p["frameHashes"]),
        "fingerprintsIdentical": sum(1 for p in pages if p["fingerprint"]),
        "pages": pages,
    }


def row_for(entry: dict[str, Any], outcome: dict[str, Any] | None) -> dict[str, Any]:
    """One result row: the page's truth parameters + what FlashTrace said (SKIP when not audited)."""
    row: dict[str, Any] = {"id": entry["id"], **{key: entry[key] for key in TRUTH_FIELDS},
                           "expected": entry["expected"], "truthCulprits": list(entry["culprits"])}
    if outcome is None:
        row.update({"verdict": "SKIP", "reason": entry["expected"].get("skipReason", "")})
    else:
        row.update({key: value for key, value in outcome.items() if key != "frameHashes"})
    return row


# --------------------------------------------------------------------------------------- machine
def commit_hash() -> str:
    """Short commit of the repository, with '-dirty' when the working tree has uncommitted changes."""
    try:
        head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO_ROOT, capture_output=True,
                              text=True, check=True, timeout=30).stdout.strip()
        status = subprocess.run(["git", "status", "--porcelain"], cwd=REPO_ROOT, capture_output=True,
                                text=True, check=True, timeout=30).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown (git not available)"
    return head + ("-dirty" if status else "")


def chromium_version() -> str:
    """Version string of the Chromium that Playwright launches."""
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                return str(browser.version)
            finally:
                browser.close()
    except Exception as exc:  # noqa: BLE001 - the version is informative only
        return f"unknown ({first_line(exc)})"


def machine_info() -> dict[str, object]:
    return {"platform": platform.platform(), "python": platform.python_version(),
            "chromium": chromium_version(), "cpus": os.cpu_count()}


# --------------------------------------------------------------------------------------- run
def run_bench(
    n: int = 200,
    seed: int = 1,
    duration: float = 10.0,
    fps: int = 60,
    workers: int = 4,
    attribute: str = "none",
    determinism_sample: int = 20,
    out: str | Path = "docs/RESULTS.md",
    regenerate: bool = False,
    pages: str | Path = "bench/pages",
    truth: str | Path = "bench/truth.json",
    work_dir: str | Path = ".flashtrace/bench",
    command_line: str | None = None,
    quiet: bool = False,
    keep_frames: bool = False,
) -> dict[str, Any]:
    """Run the benchmark and write the per-page JSON + the Markdown summary.  Returns the results dict."""
    config = BenchConfig(n=n, seed=seed, duration_s=float(duration), fps=fps, workers=workers, attribute=attribute,
                         determinism_sample=determinism_sample, out=Path(out), regenerate=regenerate,
                         pages=Path(pages), truth=Path(truth), work_dir=Path(work_dir),
                         command_line=command_line or shlex.join([Path(sys.argv[0]).name] + sys.argv[1:]),
                         quiet=quiet, keep_frames=keep_frames)
    problems = config.problems() + AuditSettings(duration_s=config.duration_s, fps=fps, attribute=attribute).problems()
    if problems:
        raise AuditInputError("; ".join(problems))
    find_attributor(attribute)  # fail once, up front, instead of once per page
    truth_data = ensure_truth(config)
    # The truth file's window numbers assume one viewport (1024x768); audit at exactly that size.
    width, height = (int(v) for v in truth_data["window"]["viewportPx"])
    settings = AuditSettings(duration_s=config.duration_s, viewport=(width, height), fps=fps, attribute=attribute)
    entries = truth_data["pages"][:n]
    audited = [e for e in entries if e["expected"]["verdict"] != "SKIP"]
    processes = pool_size(config.workers, len(audited))
    first, second = audit_all(config, audited, settings, processes)
    outcomes = {o["id"]: o for o in first}
    rows = [row_for(e, outcomes.get(e["id"])) for e in entries]
    results = build_results(config, settings, rows, compare_runs(first, second), processes)
    results["resultsJsonSha256"] = write_outputs(config, results)
    results["summaryLines"] = summary_lines(results)
    return results


def pool_size(workers: int, audited_count: int) -> int:
    """How many worker processes really run: never more than there are pages to audit."""
    return min(workers, audited_count)


def audit_all(config: BenchConfig, audited: list[dict[str, Any]], settings: AuditSettings,
              processes: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """First pass over every audited page, then the determinism re-run, in one spawn pool of
    ``processes`` workers (see pool_size)."""
    if not audited:
        return [], []
    truth_dir = config.truth.parent
    first_tasks = page_tasks(audited, truth_dir, settings, config.work_dir / "run1", config.keep_frames)
    repeat_tasks = page_tasks(audited[:config.determinism_sample], truth_dir, settings,
                              config.work_dir / "run2", config.keep_frames)
    context = multiprocessing.get_context("spawn")
    with context.Pool(processes=processes) as pool:
        first = run_tasks(pool, first_tasks, "audit", config)
        second = run_tasks(pool, repeat_tasks, "repeat", config) if repeat_tasks else []
    return first, second


def results_json_path(out: Path) -> Path:
    """Where the per-page JSON goes: next to the Markdown, same name, .json suffix.

    docs/RESULTS.md -> docs/RESULTS.json, so the committed summary cites a file that is committed with
    it and that only a run writing that same summary replaces.
    """
    target = out.with_suffix(".json")
    # An --out that already ends in .json would make the JSON overwrite the Markdown; keep them apart.
    return target if target != out else out.with_name(out.stem + ".rows.json")


def build_results(config: BenchConfig, settings: AuditSettings, rows: list[dict[str, Any]],
                  determinism: dict[str, Any], processes: int) -> dict[str, Any]:
    audited = [r for r in rows if r["verdict"] != "SKIP"]
    return {
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "commit": commit_hash(),
        "commandLine": config.command_line,
        "machine": machine_info(),
        "settings": {
            "n": config.n, "seed": config.seed, "duration": config.duration_s, "fps": config.fps,
            "viewport": list(settings.viewport), "strategy": settings.strategy, "redRule": settings.red_rule,
            "attribute": config.attribute, "workers": config.workers, "poolProcesses": processes,
            "determinismSample": config.determinism_sample, "truth": str(config.truth), "pages": str(config.pages),
        },
        "counts": {"pages": len(rows), "audited": len(audited), "skipped": len(rows) - len(audited),
                   "errors": sum(1 for r in audited if r["verdict"] == "ERROR")},
        "scores": score_all(rows, config.duration_s, config.attribute),
        "determinism": determinism,
        "rows": rows,
        "resultsJson": str(results_json_path(config.out)),
        "resultsMd": str(config.out),
    }


def write_outputs(config: BenchConfig, results: dict[str, Any]) -> str:
    """Write the per-page JSON next to the Markdown, a copy in the work directory, then the Markdown.

    Returns the sha256 of the JSON text.  The hash is printed in the Markdown's Run table (it cannot be
    stored inside the JSON it describes), so a reader can check which data produced the page.
    """
    text = json.dumps(results, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    json_path = results_json_path(config.out)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(text, encoding="utf-8")
    # Convenience copy only: every run with this work directory overwrites it.
    config.work_dir.mkdir(parents=True, exist_ok=True)
    (config.work_dir / WORK_DIR_RESULTS_NAME).write_text(text, encoding="utf-8")
    write_results_md({**results, "resultsJsonSha256": digest}, config.out)
    return digest


def summary_lines(results: dict[str, Any]) -> list[str]:
    """What `flashtrace bench` prints when it finishes."""
    counts, scores, det = results["counts"], results["scores"], results["determinism"]
    accuracy = scores["clear"]["accuracy"]
    return [
        f"bench: {counts['audited']} pages audited, {counts['skipped']} skipped, {counts['errors']} errors "
        f"({results['settings']['duration']:g} s per page)",
        f"clear-case verdict accuracy: {ratio_text(accuracy['correct'], accuracy['total'])}",
        f"determinism: {det['identical']}/{det['compared']} compared pages identical on a second run "
        f"({det['notCompared']} of {det['total']} sampled pages not compared)",
        f"results: {results['resultsMd']} and {results['resultsJson']} (sha256 {results['resultsJsonSha256']})",
    ]
