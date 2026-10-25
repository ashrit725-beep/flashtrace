"""Attribution: which element makes the page flash? (SPEC.md section 7, ADR-013)

``attribute(url, analysis, capture_meta, settings, job_dir, mode)`` returns the report's ``culprits``
(``engine/schema/report.schema.json``), ranked from 1. It works in two stages.

**Stage 1, bounding boxes (mode "bbox").** ``capture/inspect.py`` loads the page again, puts it in the
state of each violation's first event frame and lists candidate elements with their boxes. The page time
of that frame follows the strategy that captured the audit: strategy B's pre-roll is walked first, and
for strategy C (whose frame 0 is at an uncontrolled real time) a limitation is recorded. Each box is
turned into 4 px cells and compared with the cells that actually flashed in that violation
(``AnalysisResult.violation_cells``). The score is the *intersection over union* (IoU):

    overlap = |box cells AND flashing cells| / |box cells OR flashing cells|

Why IoU and not "how much of the flashing area does the box cover"? Because coverage rewards anything
big: ``<main>``, a wrapper ``<div>`` or a full-page overlay covers every flashing cell and would score
100%, just like the element that really flashes. IoU divides by the union, so a box that is mostly
*not* flashing (an ancestor around the real culprit) gets a low score, and the best score goes to the box
that matches the flashing cells most closely. Candidates are ranked by IoU (highest first); ties go to
the smaller box, then to an element that can flash by itself (it has an animation, or it is a video,
canvas, animated image or iframe), then to document order. Candidates with IoU 0 are dropped.

**Stage 2, ablation (mode "ablate").** Geometry cannot tell apart elements that cover the same cells
(an element and a static picture under it, or several elements that flash in sync). So for the top
k <= 6 candidates the page is captured again with that candidate suppressed (``capture(...,
suppress_selectors=[selector])``; ADR-013), analysed with the engine, and scored:

    ablationDelta = (qualifying general + red events before) - (qualifying events with it suppressed)

Suppressing an element stops only what that element itself does (``capture/page_scripts.py``): its own
animations are held at their frame-0 state; a media element (video, canvas, img, svg, iframe) is hidden
with everything it shows; an element without an animation of its own, which only page script changes,
has its own paint hidden. Its children and the elements inside its shadow root keep running, because they
are candidates of their own. So a wrapper around a flashing element scores 0 instead of sharing that
element's ablationDelta. Media is the exception: what it shows is its own picture, so an ``<svg>``
around an animated shape scores like the shape.

Then a *minimal culprit set* is built greedily: candidates are added in order of ablationDelta (ties by
rank) until suppressing all of them together makes the engine PASS. Members are then dropped one at a
time while the page still passes without them, and that pass is repeated until it drops nothing.
Repeating matters because suppressing more elements does not always remove more flashes (hiding a
picture can uncover flashing content under it), so a member kept early can become unnecessary once a
later one is dropped. What is left is minimal: the page passes with the set suppressed and fails when
any one member is left out. When even all top-k candidates together do not make the page pass, no set
is marked and ``attribution.json`` says so (``limitations``).

A cancellation (the server's DELETE) reaches this module through ``cancel_check``, which is called
before every inspected frame and every re-captured frame; it stops the work by raising.

Everything this stage did is written to ``<job_dir>/attribution.json``: the inspected frames, the
candidate count, every re-capture (suppressed selectors, verdict, qualifying counts, frame hash) and the
number of re-captures. Re-capture frames go to ``<job_dir>/ablation/<n>/frames`` and are deleted as soon
as they are analysed; only their hashes are kept.
"""

from __future__ import annotations

import json
import math
import os
import shutil
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from capture.capturer import capture, launch_chromium
from capture.inspect import Candidate, InspectTarget, collect_candidates
from capture.page_scripts import DEEP_SELECTOR_SEPARATOR
from capture.strategies import FALLBACK_ORDER
from capture.strategies.base import CaptureSettings
from capture.strategies.virtual_time import PREROLL_MS
from engine import AnalysisResult, analyze

if TYPE_CHECKING:  # type hints only: the capture layer does not depend on the CLI at run time
    from playwright.sync_api import Browser

    from cli.jobs import AuditSettings

#: SPEC 7: "for the top k <= 6, re-capture with the candidate suppressed".
MAX_ABLATION_CANDIDATES = 6
ATTRIBUTION_MODES: tuple[str, ...] = ("bbox", "ablate")
ATTRIBUTION_JSON_NAME = "attribution.json"
ABLATION_DIR_NAME = "ablation"
INSPECT_DIR_NAME = "inspect"
#: Written to attribution.json "limitations" (the report builder copies it) when no set makes the page pass.
NO_MINIMAL_SET_NOTE = f"attribution: no minimal set within the top {MAX_ABLATION_CANDIDATES} candidates"
#: Decimal places of the overlap written to the report (ranking uses the exact value).
OVERLAP_DECIMALS = 4
#: Written to attribution.json "limitations" when the audit's frames came from strategy C (realtime).
REALTIME_INSPECTION_NOTE = (
    "attribution: the frames were captured in real time (strategy C), so frame 0 is not at a known page "
    "time; candidates were collected with frame i at i*1000/fps ms after load, and an element that "
    "appears or moves over time may be missing from the culprit list"
)
#: Written to attribution.json "limitations" when a culprit sits inside a shadow root or a frame.
DEEP_SELECTOR_NOTE = (
    "attribution: a culprit selector containing '>>>' names an element inside a shadow root or a "
    "same-origin frame (the part before '>>>' is its host or frame element); a stylesheet of the page "
    "cannot reach it, so a CSS fix for it belongs in that shadow root's or frame's own styles"
)

#: progress(re-captures done, re-captures planned so far, label); planned grows during the greedy step.
AblationProgress = Callable[[int, int, str], None]
#: Called before every inspected and re-captured frame; raises to cancel (the server's JobCancelled).
CancelCheck = Callable[[], None]


# --------------------------------------------------------------------------------------- geometry
def _cell_span(start: float, length: float, cell_px: int, count: int) -> tuple[int, int]:
    """First cell index and one past the last that the span [start, start + length) overlaps.

    Cell i covers pixels [i*cell_px, (i+1)*cell_px). A cell the element only partly covers still counts:
    the engine averages the cell's 16 pixels (SPEC 5.2), so a flashing element that covers a quarter of a
    cell still swings that cell's luminance. The span is clamped to the grid.
    """
    if length <= 0:
        return 0, 0
    first = math.floor(start / cell_px)
    end = math.ceil((start + length) / cell_px)
    return max(0, first), min(count, end)


def rect_cells(rect: dict[str, float], grid: tuple[int, int], cell_px: int) -> np.ndarray:
    """bool [cells_y, cells_x]: the cells of the analysis grid that ``rect`` (CSS px) overlaps."""
    mask = np.zeros(grid, dtype=bool)
    x0, x1 = _cell_span(rect["x"], rect["w"], cell_px, grid[1])
    y0, y1 = _cell_span(rect["y"], rect["h"], cell_px, grid[0])
    if x0 < x1 and y0 < y1:
        mask[y0:y1, x0:x1] = True
    return mask


def intersection_over_union(a: np.ndarray, b: np.ndarray) -> float:
    """|a AND b| / |a OR b| for two bool masks; 0 when both are empty."""
    union = int(np.count_nonzero(a | b))
    return int(np.count_nonzero(a & b)) / union if union else 0.0


# --------------------------------------------------------------------------------------- stage 1: bbox
@dataclass
class Ranked:
    """A candidate with its best stage-1 score over all violations."""

    candidate: Candidate
    overlap: float   # IoU with the flashing cells of the violation where it scored best
    violation: int   # index of that violation in analysis.violations

    @property
    def area_px(self) -> float:
        return max(0.0, self.candidate.rect["w"]) * max(0.0, self.candidate.rect["h"])

    def rank_key(self) -> tuple[float, float, int, int]:
        """IoU high first; then smaller box; then an element that can flash by itself; then document order."""
        return (-self.overlap, self.area_px, 0 if self.candidate.can_flash_itself else 1, self.candidate.order)


def inspection_targets(analysis: AnalysisResult) -> list[InspectTarget]:
    """One target per violation: its first event frame and its window (SPEC 5.7)."""
    targets = []
    for violation in analysis.violations:
        frame = violation.frame_indices[0] if violation.frame_indices else violation.start
        box = violation.window
        targets.append(InspectTarget(frame=int(frame), window=(box.x, box.y, box.w, box.h)))
    return targets


def rank_candidates(per_violation: Sequence[Sequence[Candidate]], analysis: AnalysisResult) -> list[Ranked]:
    """Score every candidate against its violation's flashing cells; keep IoU > 0, best per selector, ranked."""
    if len(analysis.violation_cells) != len(analysis.violations):
        raise ValueError("analysis has no violation_cells for its violations; re-run engine.analyze")
    best: dict[str, Ranked] = {}
    for index, candidates in enumerate(per_violation):
        flashing = analysis.violation_cells[index]
        for candidate in candidates:
            cells = rect_cells(candidate.rect, analysis.grid, analysis.cell_px)
            overlap = intersection_over_union(cells, flashing)
            current = best.get(candidate.selector)
            if overlap > 0 and (current is None or overlap > current.overlap):
                best[candidate.selector] = Ranked(candidate, overlap, index)
    return sorted(best.values(), key=Ranked.rank_key)


# --------------------------------------------------------------------------------------- stage 2: ablation
@dataclass
class AblationRun:
    """One re-capture with some candidates suppressed, as analysed by the engine."""

    selectors: list[str]
    verdict: str              # engine verdict on the re-capture, or "ERROR" when it could not be made
    qualifying_general: int
    qualifying_red: int
    deterministic: bool
    strategy: str
    frame_hash: str
    frame_count: int
    error: str | None = None

    @property
    def qualifying(self) -> int:
        return self.qualifying_general + self.qualifying_red

    @property
    def passes(self) -> bool:
        return self.verdict == "PASS"

    def to_json(self) -> dict[str, Any]:
        return {"selectors": list(self.selectors), "verdict": self.verdict, "qualifyingGeneral": self.qualifying_general,
                "qualifyingRed": self.qualifying_red, "deterministic": self.deterministic, "strategy": self.strategy,
                "hash": self.frame_hash, "frameCount": self.frame_count, "error": self.error}


class Ablator:
    """Makes ablation re-captures and remembers them, so each set of suppressed selectors is captured once."""

    def __init__(self, url: str, settings: CaptureSettings, red_rule: str, strategy: str, browser: Browser,
                 job_dir: Path, progress: AblationProgress | None = None,
                 cancel_check: CancelCheck | None = None) -> None:
        self.url, self.settings, self.red_rule, self.strategy = url, settings, red_rule, strategy
        self.browser, self.job_dir, self.progress = browser, job_dir, progress
        self.cancel_check = cancel_check
        self.runs: list[AblationRun] = []
        self._by_key: dict[tuple[str, ...], AblationRun] = {}
        self.planned = 0

    def run(self, selectors: Sequence[str]) -> AblationRun:
        """The re-capture with exactly these selectors suppressed (captured now unless done before)."""
        key = tuple(sorted(selectors))
        if key not in self._by_key:
            self.planned = max(self.planned, len(self.runs) + 1)
            run = self._recapture(list(selectors), len(self.runs) + 1)
            self._by_key[key] = run
            self.runs.append(run)
            if self.progress is not None:
                self.progress(len(self.runs), self.planned, f"ablation {len(self.runs)}: suppressed {list(selectors)}")
        return self._by_key[key]

    def frame_hook(self, number: int) -> Callable[[int, int], None]:
        """The per-frame callback of re-capture ``number``.

        It runs the cancel check, then tells ``progress`` where the re-capture is. The counts stay
        (re-captures done, planned) while a re-capture runs; only the label names the frame. A cancel
        check or progress callback that raises stops the capture at that frame (``capture`` closes its
        browser context on the way out).
        """
        def on_frame(done: int, total: int) -> None:
            if self.cancel_check is not None:
                self.cancel_check()
            if self.progress is not None:
                self.progress(len(self.runs), self.planned, f"ablation {number}: frame {done}/{total}")

        return on_frame

    def _recapture(self, selectors: list[str], number: int) -> AblationRun:
        """Capture into ablation/<number>/frames, analyse, then delete the frames (only the hash is kept)."""
        folder = self.job_dir / ABLATION_DIR_NAME / str(number)
        s = self.settings
        try:
            result = capture(self.url, duration_s=s.duration_s, viewport=s.viewport, fps=s.fps,
                             interaction_script=s.interaction_script, strategy=self.strategy,
                             out_dir=folder / "frames", browser=self.browser, suppress_selectors=selectors,
                             progress=self.frame_hook(number))
            found = analyze(result.frames, fps=s.fps, viewport=s.viewport, red_rule=self.red_rule)
        except Exception as exc:  # noqa: BLE001 - one failed re-capture must not stop attribution
            return AblationRun(selectors, "ERROR", 0, 0, False, self.strategy, "", 0, _first_line(exc))
        finally:
            shutil.rmtree(folder, ignore_errors=True)
        meta = result.meta
        return AblationRun(selectors, found.verdict, len(found.qualifying_general), len(found.qualifying_red),
                           bool(meta.deterministic), meta.strategy, meta.hash, meta.frame_count)


def prune(chosen: list[str], ablator: Ablator) -> list[str]:
    """Drop members whose removal still lets the page pass, until a whole pass drops none.

    One pass is not always enough: suppressing more elements does not always remove more flashes, so a
    member kept early in a pass can become unnecessary once a later member is dropped. At the end every
    member has been tested against the final set, so leaving any one of them out makes the page fail.
    Sets already captured come from the ablator's memory, so a repeated pass costs only new sets.
    """
    dropped = True
    while dropped:
        dropped = False
        for selector in list(chosen):
            trial = [s for s in chosen if s != selector]
            if trial and ablator.run(trial).passes:  # the empty set is the original page, which fails
                chosen, dropped = trial, True
    return chosen


def greedy_minimal_set(order: Sequence[str], ablator: Ablator) -> list[str] | None:
    """Add selectors in ``order`` until suppressing them all passes, then prune; None if that never happens."""
    chosen: list[str] = []
    for selector in order:
        chosen.append(selector)
        if ablator.run(chosen).passes:
            return prune(chosen, ablator)
    return None


@dataclass
class AblationOutcome:
    """Stage 2 results: ablationDelta per selector (None when its re-capture failed) and the minimal set."""

    deltas: dict[str, int | None]
    minimal_set: list[str] | None
    ablator: Ablator


def run_ablation(ranked: Sequence[Ranked], analysis: AnalysisResult, ablator: Ablator) -> AblationOutcome:
    """Suppress each of the top k candidates alone, score them, then build the greedy minimal set."""
    top = [item.candidate.selector for item in ranked[:MAX_ABLATION_CANDIDATES]]
    ablator.planned = len(top)
    before = len(analysis.qualifying_general) + len(analysis.qualifying_red)
    deltas: dict[str, int | None] = {}
    for selector in top:
        run = ablator.run([selector])
        deltas[selector] = None if run.error else before - run.qualifying
    # Highest ablationDelta first; a failed re-capture counts as lowest; ties keep the stage-1 rank.
    order = sorted(top, key=lambda s: (-deltas[s] if deltas[s] is not None else math.inf, top.index(s)))
    return AblationOutcome(deltas, greedy_minimal_set(order, ablator), ablator)


# --------------------------------------------------------------------------------------- output
def culprit_dict(rank: int, item: Ranked, outcome: AblationOutcome | None) -> dict[str, Any]:
    """One ``culprits`` item of report.schema.json."""
    candidate = item.candidate
    selector = candidate.selector
    minimal = outcome.minimal_set if outcome is not None and outcome.minimal_set else []
    return {
        "rank": rank,
        "selector": selector,
        "nodeType": candidate.node_type,
        "ftId": candidate.ft_id,
        "rect": {"x": candidate.rect["x"], "y": candidate.rect["y"],
                 "w": max(0.0, candidate.rect["w"]), "h": max(0.0, candidate.rect["h"])},
        "animation": candidate.animation,
        "overlap": round(item.overlap, OVERLAP_DECIMALS),
        "ablationDelta": outcome.deltas.get(selector) if outcome is not None else None,
        "inMinimalSet": selector in minimal,
    }


def ablation_record(outcome: AblationOutcome, analysis: AnalysisResult, baseline_hash: str) -> dict[str, Any]:
    """The "ablation" block of attribution.json."""
    ablator = outcome.ablator
    return {
        "k": len(outcome.deltas),
        "strategy": ablator.strategy,
        "duration": ablator.settings.duration_s,
        "baseline": {"qualifyingGeneral": len(analysis.qualifying_general),
                     "qualifyingRed": len(analysis.qualifying_red), "hash": baseline_hash},
        "runs": [run.to_json() for run in ablator.runs],
        "minimalSetFound": outcome.minimal_set is not None,
        "minimalSet": list(outcome.minimal_set or []),
    }


def write_record(job_dir: Path, record: dict[str, Any]) -> Path:
    """Write attribution.json atomically (a reader never sees half a file)."""
    target = job_dir / ATTRIBUTION_JSON_NAME
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(record, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, target)
    return target


# --------------------------------------------------------------------------------------- helpers
def _first_line(exc: BaseException) -> str:
    text = str(exc).strip()
    return f"{type(exc).__name__}: {text.splitlines()[0]}" if text else type(exc).__name__


def _meta_value(capture_meta: Any, attribute: str, key: str) -> Any:
    """A field of CaptureMeta, or of a report's ``capture`` dict when that is what the caller has."""
    if isinstance(capture_meta, dict):
        return capture_meta.get(key)
    return getattr(capture_meta, attribute, None)


def ablation_strategy(capture_meta: Any, settings: AuditSettings) -> str:
    """The strategy the audit's capture used (re-captures must be made the same way), else the setting."""
    used = _meta_value(capture_meta, "strategy", "strategy")
    return used if used in FALLBACK_ORDER else settings.strategy


def inspection_time_base(strategy: str) -> tuple[int, str | None]:
    """(lead-in ms, limitation or None) that put inspection frame i at the page time of the audit's frame i.

    Inspection steps the page like strategy A: frame i at i*1000/fps ms after load (after the
    interaction script, if any). Strategy B first pre-rolls the page to PREROLL_MS of page time (SPEC 6.2,
    ADR-023), so inspection walks the same lead-in. Strategy C's frame 0 is its first screencast picture,
    at a real time nobody controls: no lead-in matches it, which the limitation says.
    """
    if strategy == "virtual_time":
        return PREROLL_MS, None
    if strategy == "realtime":
        return 0, REALTIME_INSPECTION_NOTE
    return 0, None


def capture_settings_for(url: str, settings: AuditSettings, job_dir: Path | None = None) -> CaptureSettings:
    """The audit's capture conditions.

    The interaction script is the copy cli.audit saved in the job (``script.js``), so the re-captures
    run exactly the script the baseline frames were made with, even if the file was edited since. Only
    when the job has no copy is the script read from the path in the settings.
    """
    from capture.fix import JOB_SCRIPT_NAME  # imported here: capture.fix loads the CLI's job helpers

    script = None
    saved = Path(job_dir) / JOB_SCRIPT_NAME if job_dir is not None else None
    if saved is not None and saved.is_file():
        script = saved.read_text(encoding="utf-8")
    elif settings.script is not None:
        path = Path(settings.script)
        if not path.is_file():
            raise FileNotFoundError(f"interaction script {settings.script!r} is needed again for attribution")
        script = path.read_text(encoding="utf-8")
    return CaptureSettings(url=url, duration_s=float(settings.duration_s), viewport=tuple(settings.viewport),
                           fps=int(settings.fps), interaction_script=script)


@contextmanager
def browser_session(browser: Browser | None) -> Iterator[Browser]:
    """The caller's Chromium when given (it stays open), otherwise one launched and closed here."""
    if browser is not None:
        yield browser
        return
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        own = launch_chromium(playwright.chromium)
        try:
            yield own
        finally:
            own.close()


def new_record(mode: str, analysis: AnalysisResult) -> dict[str, Any]:
    """attribution.json before any work: the mode and what each violation's inspection will look at."""
    targets = inspection_targets(analysis)
    return {
        "version": 1,
        "mode": mode,
        "violations": [{"type": v.type, "frame": t.frame, "window": v.window.to_dict(),
                        "flashingCells": int(np.count_nonzero(cells))}
                       for v, t, cells in zip(analysis.violations, targets, analysis.violation_cells)],
        "inspectionLeadInMs": 0, "candidatesCollected": 0, "candidates": 0, "selectorWarnings": [], "inspectionNotes": [],
        "ablation": None, "recaptures": 0, "limitations": [], "error": None,
    }


# --------------------------------------------------------------------------------------- entry point
def _bbox_stage(url: str, analysis: AnalysisResult, page: CaptureSettings, browser: Browser, job_dir: Path,
                record: dict[str, Any], strategy: str, cancel_check: CancelCheck | None) -> list[Ranked]:
    """Collect candidates at every violation's first event frame and rank them (stage 1).

    ``strategy`` is the one that captured the audit; it decides the inspection's time base.
    """
    lead_in_ms, limitation = inspection_time_base(strategy)
    if limitation is not None:
        record["limitations"].append(limitation)
    inspection = collect_candidates(url, page, inspection_targets(analysis), browser, job_dir / INSPECT_DIR_NAME,
                                    lead_in_ms=lead_in_ms, cancel_check=cancel_check)
    ranked = rank_candidates(inspection.candidates, analysis)
    record["inspectionLeadInMs"] = lead_in_ms
    record["candidatesCollected"] = sum(len(found) for found in inspection.candidates)
    record["candidates"] = len(ranked)
    record["inspectionNotes"] = inspection.notes
    record["selectorWarnings"] = [f"{item.candidate.selector} matched {item.candidate.selector_matches} elements"
                                  for item in ranked if item.candidate.selector_matches != 1]
    if any(DEEP_SELECTOR_SEPARATOR.strip() in item.candidate.selector for item in ranked):
        record["limitations"].append(DEEP_SELECTOR_NOTE)
    return ranked


def _ablate_stage(url: str, ranked: list[Ranked], analysis: AnalysisResult, capture_meta: Any,
                  settings: AuditSettings, page: CaptureSettings, browser: Browser, job_dir: Path,
                  record: dict[str, Any], progress: AblationProgress | None,
                  cancel_check: CancelCheck | None) -> AblationOutcome:
    """Re-capture with candidates suppressed and find the minimal set (stage 2); fills the record."""
    ablator = Ablator(url, page, settings.red_rule, ablation_strategy(capture_meta, settings), browser, job_dir,
                      progress, cancel_check)
    try:
        outcome = run_ablation(ranked, analysis, ablator)
    finally:
        record["recaptures"] = len(ablator.runs)
        folder = job_dir / ABLATION_DIR_NAME
        if folder.is_dir() and not any(folder.iterdir()):
            folder.rmdir()
    record["ablation"] = ablation_record(outcome, analysis, str(_meta_value(capture_meta, "hash", "hash") or ""))
    if outcome.minimal_set is None:
        record["limitations"].append(NO_MINIMAL_SET_NOTE)
    return outcome


def attribute(url: str, analysis: AnalysisResult, capture_meta: Any, settings: AuditSettings, job_dir: Path,
              mode: str, *, browser: Browser | None = None, progress: AblationProgress | None = None,
              cancel_check: CancelCheck | None = None) -> list[dict[str, Any]]:
    """Culprits for every violation in ``analysis`` (report.schema.json ``culprits``), ranked from 1.

    ``mode`` "bbox" ranks by box overlap only (ablationDelta null, inMinimalSet false); "ablate" also runs
    stage 2. ``browser`` (optional) is a Chromium to reuse, for callers that already hold one in this
    thread; ``progress`` (optional) is told about each ablation re-capture, and about each of its frames
    with unchanged counts; ``cancel_check`` (optional) is called before every inspected or re-captured
    frame and cancels by raising (an exception that is not an ``Exception``, like the server's
    JobCancelled, passes straight through). Also writes ``<job_dir>/attribution.json``. A failure while
    inspecting or ablating is recorded there ("error") and the culprits found so far are returned, so a
    valid verdict is never lost to attribution.
    """
    if mode not in ATTRIBUTION_MODES:
        raise ValueError(f"attribution mode must be one of {ATTRIBUTION_MODES}, got {mode!r}")
    job = Path(job_dir)
    record = new_record(mode, analysis)
    ranked: list[Ranked] = []
    outcome: AblationOutcome | None = None
    if analysis.violations:
        page = capture_settings_for(url, settings, job)
        strategy = ablation_strategy(capture_meta, settings)
        try:
            with browser_session(browser) as live:
                ranked = _bbox_stage(url, analysis, page, live, job, record, strategy, cancel_check)
                if mode == "ablate" and ranked:
                    outcome = _ablate_stage(url, ranked, analysis, capture_meta, settings, page, live, job, record,
                                            progress, cancel_check)
        except Exception as exc:  # noqa: BLE001 - recorded in attribution.json; the audit's verdict stands
            record["error"] = _first_line(exc)
    write_record(job, record)
    return [culprit_dict(rank, item, outcome) for rank, item in enumerate(ranked, start=1)]
