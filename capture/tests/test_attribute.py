"""Tests for attribution (capture/attribute.py, capture/inspect.py; SPEC 7, ADR-013).

The first half needs no browser: box-to-cell conversion, IoU, ranking ties, the greedy minimal set, the
inspection time base and cancellation, on hand-made inputs. The second half (``@pytest.mark.browser``)
audits real pages for a few seconds each: the demo site (``#loader`` is the culprit, the small
``#live-badge`` is not), benchmark pages whose truth file names the culprits (one per mechanism, plus one
multi-element page for the minimal set), and small pages written here for the cases a wrapper, a
script-driven parent or a shadow root makes hard.
"""
from __future__ import annotations

import importlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
from jsonschema import Draft202012Validator

from capture.attribute import (
    ABLATION_DIR_NAME,
    ATTRIBUTION_JSON_NAME,
    DEEP_SELECTOR_NOTE,
    MAX_ABLATION_CANDIDATES,
    NO_MINIMAL_SET_NOTE,
    REALTIME_INSPECTION_NOTE,
    Ablator,
    Ranked,
    attribute,
    capture_settings_for,
    greedy_minimal_set,
    inspection_time_base,
    intersection_over_union,
    rank_candidates,
    rect_cells,
    run_ablation,
)
from capture.inspect import Candidate, Inspection, InspectTarget, collect_candidates
from capture.strategies.base import CaptureSettings
from capture.strategies.virtual_time import PREROLL_MS
from cli.jobs import AuditSettings

attribute_module = importlib.import_module("capture.attribute")

REPO_ROOT = Path(__file__).resolve().parents[2]
DEMO_PAGE = REPO_ROOT / "demo" / "site" / "index.html"
TRUTH_PATH = REPO_ROOT / "bench" / "truth.json"
SCHEMA_PATH = REPO_ROOT / "engine" / "schema" / "report.schema.json"
DURATION_S = 3.0  # short captures keep the browser tests quick; every page flashes from the start
VIEWPORT = (1024, 768)


def culprit_validator() -> Draft202012Validator:
    """Validator for one item of the report schema's ``culprits`` array."""
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    item = dict(schema["properties"]["culprits"]["items"], **{"$defs": schema["$defs"]})
    return Draft202012Validator(item)


def candidate(selector: str, rect: tuple[float, float, float, float], order: int,
              animated: bool = False, node_type: str = "div") -> Candidate:
    x, y, w, h = rect
    return Candidate(order=order, ft_id=f"ft-{order + 1}", selector=selector, selector_matches=1, node_type=node_type,
                     rect={"x": x, "y": y, "w": w, "h": h},
                     animation={"name": "a", "duration": 250, "keyframes": []} if animated else None,
                     sources=["point"])


def fake_analysis(flashing: np.ndarray, qualifying: int = 12) -> Any:
    """The parts of an AnalysisResult that ranking and ablation read."""
    return SimpleNamespace(violations=[object()], violation_cells=[flashing], grid=flashing.shape, cell_px=4,
                           qualifying_general=list(range(qualifying)), qualifying_red=[])


# --------------------------------------------------------------------------------------- geometry
def test_cell_aligned_box_covers_exactly_its_cells() -> None:
    mask = rect_cells({"x": 20, "y": 8, "w": 40, "h": 16}, (10, 30), 4)
    assert mask.sum() == 10 * 4
    assert mask[2:6, 5:15].all()


def test_partly_covered_edge_cells_count_and_the_box_is_clamped_to_the_grid() -> None:
    # 1 px into cell 0 on the left and 1 px into cell 3 on the right: both edge cells change colour.
    assert rect_cells({"x": 3, "y": 0, "w": 10, "h": 4}, (1, 8), 4)[0].tolist() == [True] * 4 + [False] * 4
    assert rect_cells({"x": -100, "y": -100, "w": 1000, "h": 1000}, (3, 3), 4).all()
    assert not rect_cells({"x": 5, "y": 5, "w": 0, "h": 10}, (4, 4), 4).any()


def test_iou_gives_an_enclosing_ancestor_a_low_score() -> None:
    flashing = rect_cells({"x": 40, "y": 40, "w": 40, "h": 40}, (50, 50), 4)  # 100 cells
    element = rect_cells({"x": 40, "y": 40, "w": 40, "h": 40}, (50, 50), 4)
    ancestor = rect_cells({"x": 0, "y": 0, "w": 200, "h": 200}, (50, 50), 4)  # covers every flashing cell
    assert intersection_over_union(element, flashing) == 1.0
    assert intersection_over_union(ancestor, flashing) == pytest.approx(100 / 2500)
    assert intersection_over_union(np.zeros((2, 2), bool), np.zeros((2, 2), bool)) == 0.0


def test_ranking_ties_smaller_box_then_own_motion_then_document_order() -> None:
    flashing = rect_cells({"x": 40, "y": 40, "w": 40, "h": 40}, (50, 50), 4)
    found = [
        candidate("#static-picture", (40, 40, 40, 40), order=1),               # same box, no motion of its own
        candidate("#flasher", (40.5, 40.5, 39, 39), order=2, animated=True),   # same cells, smaller box
        candidate("#twin", (40, 40, 40, 40), order=3, animated=True),
        candidate("#wrapper", (0, 0, 200, 200), order=0),
        candidate("#far-away", (180, 180, 8, 8), order=4),                      # IoU 0: dropped
    ]
    ranked = rank_candidates([found], fake_analysis(flashing))
    assert [r.candidate.selector for r in ranked] == ["#flasher", "#twin", "#static-picture", "#wrapper"]


# --------------------------------------------------------------------------------------- greedy minimal set
@dataclass
class FakeRun:
    passes: bool
    qualifying: int
    error: str | None = None


class FakeAblator:
    """Suppressing a set passes when the flashing cells left are 1 360 or fewer (as on a bench page)."""

    def __init__(self, cells: dict[str, int], total_qualifying: int = 12) -> None:
        self.cells, self.total = cells, total_qualifying
        self.planned = 0
        self.calls: list[tuple[str, ...]] = []

    def run(self, selectors: Sequence[str]) -> FakeRun:
        key = tuple(sorted(selectors))
        if key not in self.calls:
            self.calls.append(key)
        left = sum(c for s, c in self.cells.items() if s not in selectors)
        return FakeRun(passes=left <= 1360, qualifying=0 if left <= 1360 else self.total)


def test_greedy_set_is_pruned_to_a_minimal_set() -> None:
    ablator = FakeAblator({"#a": 900, "#b": 1800, "#c": 600})
    chosen = greedy_minimal_set(["#c", "#a", "#b"], ablator)  # #c, then #a still fail; adding #b passes
    assert chosen == ["#a", "#b"]  # #c was not needed once #b is suppressed
    for member in chosen:
        assert not ablator.run([s for s in chosen if s != member]).passes


def test_no_minimal_set_when_suppressing_everything_still_fails() -> None:
    ablator = FakeAblator({"#a": 100, "#b": 100, "#elsewhere": 5000})
    assert greedy_minimal_set(["#a", "#b"], ablator) is None


def eight_ranked() -> tuple[list[Ranked], Any]:
    """Eight cell-aligned candidates inside one flashing block, IoU strictly falling from #e0 to #e7."""
    flashing = rect_cells({"x": 0, "y": 0, "w": 400, "h": 200}, (60, 120), 4)
    found = [candidate(f"#e{i}", (i * 48.0, 0, 44, 200 - 8 * i), order=i) for i in range(8)]
    analysis = fake_analysis(flashing)
    return rank_candidates([found], analysis), analysis


def test_ablation_tries_only_the_top_six_and_reports_when_no_set_passes() -> None:
    ranked, analysis = eight_ranked()
    assert [r.candidate.selector for r in ranked] == [f"#e{i}" for i in range(8)]
    cells = {f"#e{i}": 600 for i in range(8)}
    cells["#e7"] = 4000  # ranked 8th: outside the top six, so suppressing the top six still fails
    ablator = FakeAblator(cells)
    outcome = run_ablation(ranked, analysis, ablator)  # type: ignore[arg-type]
    assert sorted(outcome.deltas) == [f"#e{i}" for i in range(MAX_ABLATION_CANDIDATES)]
    assert set(outcome.deltas.values()) == {0}  # no single suppression changes the count
    assert outcome.minimal_set is None
    # Six single suppressions, then the greedy sets of size 2 to 6 (each captured once).
    assert len(ablator.calls) == MAX_ABLATION_CANDIDATES + (MAX_ABLATION_CANDIDATES - 1)


def test_ablation_puts_the_highest_scoring_candidate_first_whatever_its_rank() -> None:
    ranked, analysis = eight_ranked()
    cells = {f"#e{i}": 100 for i in range(8)}
    cells["#e3"] = 9500  # suppressing #e3 alone leaves 700 cells: the page passes
    ablator = FakeAblator(cells)
    outcome = run_ablation(ranked, analysis, ablator)  # type: ignore[arg-type]
    assert outcome.deltas["#e3"] == 12 and outcome.deltas["#e0"] == 0
    assert outcome.minimal_set == ["#e3"]
    assert len(ablator.calls) == MAX_ABLATION_CANDIDATES  # the single runs were enough


class ScriptedAblator:
    """Passes for exactly the listed sets: suppressing more elements need not remove more flashes."""

    def __init__(self, passing: set[frozenset[str]]) -> None:
        self.passing = passing
        self.planned = 0

    def run(self, selectors: Sequence[str]) -> FakeRun:
        passes = frozenset(selectors) in self.passing
        return FakeRun(passes=passes, qualifying=0 if passes else 12)


def test_pruning_repeats_until_every_member_is_needed() -> None:
    # {#b, #y, #c} fails although {#b, #c} passes: hiding #y uncovers something that flashes. A single
    # pruning pass keeps #a (tested while #y was still in) and returns a set that is not minimal.
    passing = {frozenset(s) for s in (("#a", "#b", "#y", "#c"), ("#a", "#b", "#c"), ("#b", "#c"))}
    ablator = ScriptedAblator(passing)
    chosen = greedy_minimal_set(["#a", "#b", "#y", "#c"], ablator)  # type: ignore[arg-type]
    assert chosen == ["#b", "#c"]
    assert ablator.run(chosen).passes
    for member in chosen:
        assert not ablator.run([s for s in chosen if s != member]).passes


def test_inspection_time_base_follows_the_strategy_that_captured_the_audit() -> None:
    assert inspection_time_base("waapi_step") == (0, None)
    assert inspection_time_base("virtual_time") == (PREROLL_MS, None)  # B's frame 0 is after its pre-roll
    assert inspection_time_base("realtime") == (0, REALTIME_INSPECTION_NOTE)  # C's frame 0 is not at a known time


def one_violation_analysis() -> Any:
    """A fake analysis with one violation whose first event is at frame 3."""
    flashing = rect_cells({"x": 40, "y": 40, "w": 40, "h": 40}, (50, 50), 4)
    window = SimpleNamespace(x=0, y=0, w=200, h=200, to_dict=lambda: {"x": 0, "y": 0, "w": 200, "h": 200})
    analysis = fake_analysis(flashing)
    analysis.violations = [SimpleNamespace(type="general", frame_indices=[3], start=0, window=window)]
    return analysis


@pytest.mark.parametrize(("strategy", "lead_in", "notes"), [
    ("waapi_step", 0, []), ("virtual_time", PREROLL_MS, []), ("realtime", 0, [REALTIME_INSPECTION_NOTE])])
def test_attribution_inspects_on_the_time_base_of_the_audit_strategy(
        strategy: str, lead_in: int, notes: list[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def fake_collect(url: str, page: Any, targets: list[InspectTarget], browser: Any, scratch: Path, *,
                     lead_in_ms: int, cancel_check: Any) -> Inspection:
        seen.update(lead_in_ms=lead_in_ms, cancel_check=cancel_check, frames=[t.frame for t in targets])
        found = candidate("html > body > live-banner >>> div", (40, 40, 40, 40), order=0, animated=True)
        return Inspection(candidates=[[found]])

    def check() -> None:
        return None

    monkeypatch.setattr(attribute_module, "collect_candidates", fake_collect)
    settings = AuditSettings(duration_s=1.0, viewport=VIEWPORT, attribute="bbox")
    culprits = attribute("file:///p.html", one_violation_analysis(), {"strategy": strategy}, settings, tmp_path,
                         "bbox", browser=object(), cancel_check=check)
    record = json.loads((tmp_path / ATTRIBUTION_JSON_NAME).read_text(encoding="utf-8"))
    assert seen == {"lead_in_ms": lead_in, "cancel_check": check, "frames": [3]}
    assert record["inspectionLeadInMs"] == lead_in and record["error"] is None
    # A culprit inside a shadow root gets a note: a page stylesheet cannot reach it.
    assert record["limitations"] == notes + [DEEP_SELECTOR_NOTE]
    assert culprits[0]["selector"] == "html > body > live-banner >>> div"


class Cancelled(BaseException):
    """Stands in for the server's JobCancelled, which is a BaseException too."""


def test_a_cancel_check_stops_an_ablation_recapture_between_frames(tmp_path: Path,
                                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    state = {"cancelled": False, "frames": 0}

    def fake_capture(url: str, **kwargs: Any) -> Any:
        for done in range(1, 6):  # a 5-frame re-capture that reports every frame, as capture() does
            state["frames"] = done
            kwargs["progress"](done, 5)
            state["cancelled"] = done == 2 or state["cancelled"]  # DELETE arrives during frame 2
        raise AssertionError("the re-capture should have stopped at the first frame after the cancel")

    def cancel_check() -> None:
        if state["cancelled"]:
            raise Cancelled()

    told: list[tuple[int, int, str]] = []
    monkeypatch.setattr(attribute_module, "capture", fake_capture)
    page = CaptureSettings(url="file:///p.html", duration_s=1.0, viewport=VIEWPORT, fps=5)
    ablator = Ablator("file:///p.html", page, "wcag22", "waapi_step", object(), tmp_path,  # type: ignore[arg-type]
                      progress=lambda done, total, label: told.append((done, total, label)), cancel_check=cancel_check)
    ablator.planned = 6
    with pytest.raises(Cancelled):
        ablator.run(["#box"])
    assert state["frames"] == 3  # stopped at frame 3, not after the whole re-capture
    assert told == [(0, 6, "ablation 1: frame 1/5"), (0, 6, "ablation 1: frame 2/5")]  # counts stay put
    assert ablator.runs == []  # a cancelled re-capture is not recorded as a failed one
    assert not (tmp_path / ABLATION_DIR_NAME / "1").exists()


def test_ranked_rank_key_uses_area_in_css_px() -> None:
    item = Ranked(candidate("#x", (0, 0, 10, 20), order=0), overlap=0.5, violation=0)
    assert item.area_px == 200.0


def test_recaptures_use_the_script_copy_kept_in_the_job(tmp_path: Path) -> None:
    # cli.audit keeps the script it ran as <job>/script.js; ablation must re-run that exact text,
    # not whatever the original file holds now (the same rule fix verification follows).
    script = tmp_path / "click.js"
    script.write_text("edited after the audit;", encoding="utf-8")
    settings = AuditSettings(duration_s=3.0, viewport=VIEWPORT, attribute="ablate", script=str(script))
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    assert capture_settings_for("file:///p.html", settings, job_dir).interaction_script == "edited after the audit;"
    (job_dir / "script.js").write_text("what the audit ran;", encoding="utf-8")
    assert capture_settings_for("file:///p.html", settings, job_dir).interaction_script == "what the audit ran;"


# --------------------------------------------------------------------------------------- browser helpers
@dataclass
class Baseline:
    url: str
    analysis: Any
    meta: Any
    job_dir: Path
    duration_s: float = DURATION_S


def audit_baseline(page: Path, job_dir: Path, browser: Any, duration_s: float = DURATION_S) -> Baseline:
    """Capture and analyse ``page`` the way an audit does, into ``job_dir/frames``."""
    from capture import capture
    from engine import analyze

    result = capture(str(page), duration_s=duration_s, viewport=VIEWPORT, browser=browser, out_dir=job_dir / "frames")
    analysis = analyze(result.frames, fps=60, viewport=VIEWPORT)
    return Baseline(result.meta.url, analysis, result.meta, job_dir, duration_s)


def ablated_verdict(page: Path, selectors: list[str], out_dir: Path, browser: Any) -> str:
    """Engine verdict on a 2 s capture of ``page`` with ``selectors`` suppressed (ADR-013).

    2 s, not 1: a 4 Hz strobe that starts after frame 0 completes only three flashes inside the first
    second, so a single one-second window would not show it failing.
    """
    from capture import capture
    from engine import analyze

    result = capture(str(page), duration_s=2.0, viewport=VIEWPORT, browser=browser, out_dir=out_dir,
                     suppress_selectors=selectors)
    assert result.meta.deterministic, result.meta.notes
    return analyze(result.frames, fps=60, viewport=VIEWPORT).verdict


def write_page(folder: Path, name: str, html: str) -> Path:
    page = folder / name
    page.write_text(html, encoding="utf-8")
    return page


def run_attribution(baseline: Baseline, mode: str, browser: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    settings = AuditSettings(duration_s=baseline.duration_s, viewport=VIEWPORT, attribute=mode)
    culprits = attribute(baseline.url, baseline.analysis, baseline.meta, settings, baseline.job_dir, mode, browser=browser)
    record = json.loads((baseline.job_dir / ATTRIBUTION_JSON_NAME).read_text(encoding="utf-8"))
    return culprits, record


def load_truth() -> dict[str, Any]:
    return json.loads(TRUTH_PATH.read_text(encoding="utf-8"))


def bench_page(entry: dict[str, Any]) -> Path:
    """The page file of a truth entry; the test is skipped when the generated pages are not there."""
    files = [TRUTH_PATH.parent / name for name in [entry["file"], *entry.get("videos", [])]]
    if not all(f.is_file() for f in files):
        pytest.skip(f"benchmark page {entry['id']} is not generated (run flashtrace bench once)")
    return files[0]


def first_clear_fail(mechanism: str, min_elements: int = 1) -> dict[str, Any]:
    """The first benchmark page of this mechanism that is expected to FAIL and is not a boundary case."""
    for entry in load_truth()["pages"]:
        expected = entry["expected"]
        if (entry["mechanism"] == mechanism and expected["verdict"] == "FAIL" and not expected["boundary"]
                and entry["elementCount"] >= min_elements):
            return entry
    raise AssertionError(f"truth file has no clear FAIL page for {mechanism}")


def truth_cells(entry: dict[str, Any]) -> dict[str, int]:
    """Flashing cells of each truth element (bench rectangles are cell aligned, ADR-014)."""
    cell = int(load_truth()["window"]["cellPx"])
    return {e["selector"]: int(e["rect"]["w"] // cell) * int(e["rect"]["h"] // cell) for e in entry["elements"]}


# --------------------------------------------------------------------------------------- demo site
@pytest.fixture(scope="module")
def demo_baseline(browser: Any, tmp_path_factory: pytest.TempPathFactory) -> Baseline:
    return audit_baseline(DEMO_PAGE, tmp_path_factory.mktemp("demo-job"), browser)


@pytest.fixture(scope="module")
def demo_ablation(demo_baseline: Baseline, browser: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    return run_attribution(demo_baseline, "ablate", browser)


@pytest.mark.browser
def test_demo_bbox_ranks_the_loader_first(demo_baseline: Baseline, browser: Any) -> None:
    assert demo_baseline.analysis.verdict == "FAIL"
    culprits, record = run_attribution(demo_baseline, "bbox", browser)
    assert record["error"] is None and record["recaptures"] == 0 and record["ablation"] is None
    assert culprits[0]["selector"] == "#loader" and culprits[0]["overlap"] >= 0.8
    assert culprits[0]["animation"]["name"] == "strobe" and culprits[0]["ftId"].startswith("ft-")
    assert culprits[0]["selector"] != "#live-badge"
    assert [c["rank"] for c in culprits] == list(range(1, len(culprits) + 1))
    assert all(c["ablationDelta"] is None and c["inMinimalSet"] is False for c in culprits)
    validator = culprit_validator()
    for item in culprits:
        validator.validate(item)


@pytest.mark.browser
def test_demo_ablation_finds_exactly_the_loader(demo_ablation: tuple[list[dict[str, Any]], dict[str, Any]],
                                                demo_baseline: Baseline) -> None:
    culprits, record = demo_ablation
    assert record["error"] is None
    loader = next(c for c in culprits if c["selector"] == "#loader")
    assert loader["rank"] == 1 and loader["ablationDelta"] > 0
    assert [c["selector"] for c in culprits if c["inMinimalSet"]] == ["#loader"]
    ablation = record["ablation"]
    assert ablation["minimalSetFound"] is True and ablation["minimalSet"] == ["#loader"]
    assert record["recaptures"] == len(ablation["runs"]) <= MAX_ABLATION_CANDIDATES + 2
    assert record["limitations"] == []
    # Re-capture frames are deleted after analysis; only their hashes stay in attribution.json.
    assert not (demo_baseline.job_dir / ABLATION_DIR_NAME).exists()
    assert all(len(run["hash"]) == 64 for run in ablation["runs"])
    validator = culprit_validator()
    for item in culprits:
        validator.validate(item)


# --------------------------------------------------------------------------------------- benchmark pages
@pytest.mark.browser
@pytest.mark.parametrize("mechanism", ["css", "waapi", "raf", "canvas", "video"])
def test_bench_top_culprit_is_a_truth_culprit(mechanism: str, browser: Any, tmp_path: Path) -> None:
    entry = first_clear_fail(mechanism)
    baseline = audit_baseline(bench_page(entry), tmp_path, browser)
    assert baseline.analysis.verdict == "FAIL", entry["id"]
    culprits, record = run_attribution(baseline, "bbox", browser)
    assert record["error"] is None, record["error"]
    assert culprits and culprits[0]["selector"] in entry["culprits"], (entry["id"], culprits[:3])


@pytest.mark.browser
def test_bench_multi_element_minimal_set_is_valid_and_minimal(browser: Any, tmp_path: Path) -> None:
    entry = first_clear_fail("raf", min_elements=3)
    baseline = audit_baseline(bench_page(entry), tmp_path, browser)
    culprits, record = run_attribution(baseline, "ablate", browser)
    assert record["error"] is None, record["error"]
    chosen = {c["selector"] for c in culprits if c["inMinimalSet"]}
    cells = truth_cells(entry)
    limit = int(load_truth()["window"]["qualifyingCells"])  # > 1 360 cells of one window qualifies

    def left_after(removed: set[str]) -> int:
        return sum(count for selector, count in cells.items() if selector not in removed)

    assert chosen and chosen <= set(entry["culprits"]), (entry["id"], chosen)
    assert left_after(chosen) <= limit
    for member in chosen:  # minimal: putting any one member back makes the page fail again
        assert left_after(chosen - {member}) > limit
    assert NO_MINIMAL_SET_NOTE not in record["limitations"]


# --------------------------------------------------------------------------------------- own-element suppression
# Three tiles flash in sync inside a wrapper that fits them exactly. One tile alone (25 x 50 cells) is
# under a quarter of the 85 x 64-cell window; any two together are over it.
SYNCED_TILES_PAGE = """<!doctype html><html><head><meta charset="utf-8"><style>
html, body { margin: 0; background: rgb(255, 255, 255); }
#wrap { position: absolute; left: 40px; top: 40px; width: 300px; height: 200px; display: flex; }
.tile { width: 100px; height: 200px; animation: strobe 250ms steps(1, end) infinite; }
@keyframes strobe { 0% { background-color: rgb(255, 255, 255); } 50% { background-color: rgb(0, 0, 0); }
                    100% { background-color: rgb(255, 255, 255); } }
</style></head><body>
<div id="wrap"><div id="t1" class="tile"></div><div id="t2" class="tile"></div><div id="t3" class="tile"></div></div>
</body></html>"""


@pytest.mark.browser
def test_ablation_credits_the_flashing_tiles_not_their_wrapper(browser: Any, tmp_path: Path) -> None:
    """Suppressing #wrap must not hold its children's animations (ADR-013: only the element's own)."""
    page = write_page(tmp_path, "tiles.html", SYNCED_TILES_PAGE)
    baseline = audit_baseline(page, tmp_path / "job", browser, duration_s=2.0)
    assert baseline.analysis.verdict == "FAIL"
    culprits, record = run_attribution(baseline, "ablate", browser)
    assert record["error"] is None, record["error"]
    wrapper = next(c for c in culprits if c["selector"] == "#wrap")
    assert wrapper["rank"] == 1 and wrapper["overlap"] == 1.0  # geometry alone prefers the wrapper
    assert wrapper["ablationDelta"] == 0 and wrapper["inMinimalSet"] is False
    chosen = {c["selector"] for c in culprits if c["inMinimalSet"]}
    assert len(chosen) == 2 and chosen <= {"#t1", "#t2", "#t3"}, chosen


# Page script strobes #overlay's background; its child #fader has a slow CSS fade of its own.
RAF_PARENT_PAGE = """<!doctype html><html><head><meta charset="utf-8"><style>
html, body { margin: 0; background: rgb(255, 255, 255); }
#overlay { position: absolute; left: 40px; top: 40px; width: 400px; height: 300px; background: rgb(255, 255, 255); }
#fader { position: absolute; left: 20px; top: 20px; width: 60px; height: 40px; background: rgb(128, 128, 128);
         animation: fade 2s linear infinite; }
@keyframes fade { from { opacity: 0.2; } to { opacity: 1; } }
</style></head><body>
<div id="overlay"><div id="fader"></div></div>
<script>
const overlay = document.getElementById('overlay');
let start = null;
function tick(now) {  // the overlay's background flips every 125 ms
  if (start === null) start = now;
  overlay.style.backgroundColor = Math.floor((now - start) / 125) % 2 ? 'rgb(0, 0, 0)' : 'rgb(255, 255, 255)';
  requestAnimationFrame(tick);
}
requestAnimationFrame(tick);
</script></body></html>"""


@pytest.mark.browser
def test_a_script_driven_parent_is_hidden_even_with_an_animated_child(browser: Any, tmp_path: Path) -> None:
    """ADR-013 (b): #overlay has no animation of its own, so its paint is hidden; its child keeps fading."""
    from capture import capture
    from engine import analyze

    page = write_page(tmp_path, "raf-parent.html", RAF_PARENT_PAGE)
    assert ablated_verdict(page, [], tmp_path / "whole", browser) == "FAIL"
    result = capture(str(page), duration_s=2.0, viewport=VIEWPORT, browser=browser, out_dir=tmp_path / "ablated",
                     suppress_selectors=["#overlay"])
    assert result.meta.deterministic, result.meta.notes
    assert analyze(result.frames, fps=60, viewport=VIEWPORT).verdict == "PASS"
    assert {tuple(frame[300, 300].tolist()) for frame in result.frames} == {(255, 255, 255)}  # white page shows
    assert len({int(frame[80, 90, 0]) for frame in result.frames}) > 10  # the child's fade was not held
    assert any(note.endswith("1 element(s) without animations hidden as script-driven") for note in result.meta.notes)


# --------------------------------------------------------------------------------------- shadow DOM selectors
# The strobing <div> sits inside an open shadow root, inside a .frame <div> that has an invisible slow
# animation (an outline colour with no outline), so both are animation candidates. A light-DOM div.card
# elsewhere would match a root-relative 'div'.
SHADOW_PAGE = """<!doctype html><html><head><meta charset="utf-8"><style>
html, body { margin: 0; background: rgb(255, 255, 255); }
.card { position: absolute; left: 600px; top: 500px; width: 120px; height: 40px; background: rgb(230, 230, 230); }
live-banner { position: absolute; left: 40px; top: 40px; width: 400px; height: 300px; display: block; }
</style></head><body>
<div class="card"></div>
<live-banner></live-banner>
<script>
customElements.define('live-banner', class extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: 'open' }).innerHTML = `<style>
      .frame { width: 400px; height: 300px; animation: idle 10s linear infinite; }
      .frame > div { width: 400px; height: 300px; animation: strobe 250ms steps(1, end) infinite; }
      @keyframes idle { from { outline-color: rgb(0, 0, 0); } to { outline-color: rgb(9, 9, 9); } }
      @keyframes strobe { 0% { background-color: rgb(255, 255, 255); } 50% { background-color: rgb(0, 0, 0); }
                          100% { background-color: rgb(255, 255, 255); } }
    </style><div class="frame"><div></div></div>`;
  }
});
</script></body></html>"""
HOST = "html > body > live-banner"


@pytest.mark.browser
def test_shadow_dom_culprits_get_page_wide_unique_selectors(browser: Any, tmp_path: Path) -> None:
    page = write_page(tmp_path, "shadow.html", SHADOW_PAGE)
    baseline = audit_baseline(page, tmp_path / "job", browser, duration_s=2.0)
    culprits, record = run_attribution(baseline, "bbox", browser)
    assert record["error"] is None and record["selectorWarnings"] == []  # each selector names one element
    strobe, frame = f"{HOST} >>> div > div", f"{HOST} >>> :host > div"  # ':host >' anchors the top level
    assert {c["selector"] for c in culprits} == {HOST, frame, strobe}
    assert DEEP_SELECTOR_NOTE in record["limitations"]
    # Ablation reads the same selectors: holding the shadow element's own animation stops the strobe,
    # while suppressing the host (no animation of its own: its paint is hidden) leaves its shadow content running.
    assert ablated_verdict(page, [strobe], tmp_path / "inner", browser) == "PASS"
    assert ablated_verdict(page, [HOST], tmp_path / "host", browser) == "FAIL"


# --------------------------------------------------------------------------------------- inspection time base
# A timer adds the flashing element 1 s after load: before strategy B's frame 0, after strategy A's.
LATE_PAGE = """<!doctype html><html><head><meta charset="utf-8"><style>
html, body { margin: 0; background: rgb(255, 255, 255); }
#wrap { position: absolute; left: 0; top: 0; width: 600px; height: 500px; }
#late { width: 600px; height: 500px; background: rgb(0, 0, 0); animation: blink 100ms steps(1) infinite; }
@keyframes blink { 50% { background: rgb(255, 255, 255); } }
</style></head><body><div id="wrap"></div>
<script>
setTimeout(() => {
  const late = document.createElement('div');
  late.id = 'late';
  document.getElementById('wrap').appendChild(late);
}, 1000);
</script></body></html>"""


def late_settings(page: Path) -> CaptureSettings:
    return CaptureSettings(url=str(page), duration_s=1.0, viewport=VIEWPORT, fps=30)


@pytest.mark.browser
@pytest.mark.parametrize(("lead_in_ms", "finds_late"), [(0, False), (PREROLL_MS, True)])
def test_inspection_walks_strategy_b_pre_roll_before_frame_0(lead_in_ms: int, finds_late: bool, browser: Any,
                                                             tmp_path: Path) -> None:
    page = write_page(tmp_path, "late.html", LATE_PAGE)
    found = collect_candidates(str(page), late_settings(page), [InspectTarget(frame=0, window=(0, 0, 600, 500))],
                               browser, tmp_path / "inspect", lead_in_ms=lead_in_ms)
    selectors = [c.selector for c in found.candidates[0]]
    assert ("#late" in selectors) is finds_late, selectors


@pytest.mark.browser
def test_a_cancel_check_stops_the_inspection_walk(browser: Any, tmp_path: Path) -> None:
    page = write_page(tmp_path, "late.html", LATE_PAGE)
    calls: list[int] = []

    def cancel_check() -> None:
        calls.append(len(calls))
        if len(calls) == 5:
            raise Cancelled()

    with pytest.raises(Cancelled):
        collect_candidates(str(page), late_settings(page), [InspectTarget(frame=20, window=(0, 0, 600, 500))],
                           browser, tmp_path / "inspect", cancel_check=cancel_check)
    assert len(calls) == 5  # stopped at the fifth frame, long before frame 20
    assert not (tmp_path / "inspect").exists()
