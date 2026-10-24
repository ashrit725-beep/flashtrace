"""Tests for the synthetic benchmark generator (bench/).

They check what the brief asks for: byte-identical output for the same seed,
a truth file whose verdicts follow from its own parameters, cell-aligned and
non-overlapping geometry, colours that match the WCAG formula, video pages
that produce WebM files (or are skipped the documented way), and the
boundary rules on hand-picked parameter sets.
"""
import dataclasses
import hashlib
import json
import subprocess
import sys
import warnings
from itertools import combinations
from pathlib import Path

import pytest

from bench import colors, geometry, params
from bench import ffmpeg as ffmpeg_tools
from bench import generate as generator
from bench.params import PageParams
from bench.truth import expected_outcome

SMALL_N = 15
SEED = 1
CELL = params.CELL_PX
WINDOW_CELLS = params.WINDOW_CELL_COUNT          # 5440
QUALIFYING = params.QUALIFYING_CELLS             # 1360
WINDOW_W_PX, WINDOW_H_PX = (params.WINDOW_CELLS[0] * CELL, params.WINDOW_CELLS[1] * CELL)

REQUIRED_KEYS = {"id", "file", "mechanism", "rateHz", "areaFraction", "totalCells", "elementCount",
                 "red", "deltaLTarget", "deltaLActual", "colors", "background", "distractors",
                 "elements", "expected", "culprits"}
EXPECTED_KEYS = {"verdict", "types", "advisory", "boundary", "boundaryReasons"}


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def gap_px(a, b):
    """Largest axis gap between two px rects; >= 0 means they do not overlap."""
    horizontal = max(b["x"] - (a["x"] + a["w"]), a["x"] - (b["x"] + b["w"]))
    vertical = max(b["y"] - (a["y"] + a["h"]), a["y"] - (b["y"] + b["h"]))
    return max(horizontal, vertical)


def rederived_expectation(page):
    """The brief's verdict rules, written out again independently of bench/truth.py."""
    flashes = page["totalCells"] > 1360 and (page["deltaLActual"] >= 0.10 or page["red"])
    fail = page["rateHz"] > 3 and flashes
    types = []
    if fail and page["deltaLActual"] >= 0.10:
        types.append("general")
    if fail and page["red"]:
        types.append("red")
    advisory = 2 <= page["rateHz"] <= 3 and flashes
    return ("FAIL" if fail else "PASS"), types, advisory


@pytest.fixture(scope="module")
def generated(tmp_path_factory):
    root = tmp_path_factory.mktemp("bench")
    out_dir = root / "pages"
    truth_path = root / "truth.json"
    truth = generator.generate(n=SMALL_N, seed=SEED, out_dir=out_dir, truth_path=truth_path)
    return out_dir, truth_path, truth


# 1. Determinism -----------------------------------------------------------------

def test_same_seed_gives_byte_identical_html_and_truth(generated, tmp_path_factory):
    out_a, truth_a, _ = generated
    root = tmp_path_factory.mktemp("again")
    out_b, truth_b = root / "pages", root / "truth.json"
    generator.generate(n=SMALL_N, seed=SEED, out_dir=out_b, truth_path=truth_b)

    names = sorted(path.name for path in out_a.glob("*.html"))
    assert len(names) == SMALL_N
    assert names == sorted(path.name for path in out_b.glob("*.html"))
    for name in names:
        assert sha256(out_a / name) == sha256(out_b / name), name
    assert sha256(truth_a) == sha256(truth_b)

    # The brief asks whether WebM output is byte-identical too; report, do not fail.
    webms = sorted(path.name for path in out_a.glob("*.webm"))
    differing = [name for name in webms if sha256(out_a / name) != sha256(out_b / name)]
    if differing:
        warnings.warn(f"WebM files differ between two identical runs: {differing}")


def test_different_seed_changes_the_truth(tmp_path):
    a = generator.generate(n=3, seed=1, out_dir=tmp_path / "a" / "pages", ffmpeg=None)
    b = generator.generate(n=3, seed=2, out_dir=tmp_path / "b" / "pages", ffmpeg=None)
    strip = lambda truth: [{k: v for k, v in page.items() if k != "file"} for page in truth["pages"]]
    assert strip(a) != strip(b)


# 2. Truth schema, verdict logic, geometry -----------------------------------------

def test_truth_schema_and_rederived_verdicts(generated):
    _, truth_path, truth = generated
    assert truth["seed"] == SEED and truth["n"] == SMALL_N and len(truth["pages"]) == SMALL_N
    assert truth["window"]["windowCellCount"] == WINDOW_CELLS
    assert truth["window"]["qualifyingCells"] == QUALIFYING
    assert "generatedAt" not in truth
    for page in truth["pages"]:
        assert REQUIRED_KEYS <= page.keys(), page["id"]
        assert EXPECTED_KEYS <= page["expected"].keys(), page["id"]
        html_path = truth_path.parent / page["file"]
        assert html_path.is_file()
        assert page["rateHz"] in params.RATES_HZ
        assert page["areaFraction"] in params.AREA_FRACTIONS
        assert page["deltaLTarget"] in params.DELTA_L_TARGETS
        assert page["elementCount"] in params.ELEMENT_COUNTS
        assert page["background"] in ([255, 255, 255], [240, 240, 240])
        assert set(page["distractors"]) <= set(params.DISTRACTORS)
        if page["mechanism"] == "video-skipped":
            assert page["expected"]["verdict"] == "SKIP" and page["culprits"] == []
            continue
        assert page["mechanism"] in params.MECHANISMS
        verdict, types, advisory = rederived_expectation(page)
        assert page["expected"]["verdict"] == verdict, page["id"]
        assert page["expected"]["types"] == types, page["id"]
        assert page["expected"]["advisory"] == advisory, page["id"]
        assert page["expected"]["boundary"] == bool(page["expected"]["boundaryReasons"])
        selectors = [element["selector"] for element in page["elements"]]
        assert page["culprits"] == selectors
        assert selectors == [f"#flash-{k}" for k in range(1, page["elementCount"] + 1)]


def test_page_embeds_its_own_truth_entry(generated):
    _, truth_path, truth = generated
    for page in truth["pages"]:
        html = (truth_path.parent / page["file"]).read_text(encoding="utf-8")
        start = html.index('<script type="application/json" id="bench-truth">') + len(
            '<script type="application/json" id="bench-truth">')
        end = html.index("</script>", start)
        embedded = json.loads(html[start:end].replace("<\\/", "</"))
        assert embedded == page
        for selector in page["culprits"]:
            assert f'id="{selector.lstrip("#")}" class="flash" data-bench-culprit' in html
        assert "http://" not in html and "https://" not in html      # no external resources


def test_geometry_is_cell_aligned_inside_one_window_and_non_overlapping(generated):
    _, _, truth = generated
    for page in truth["pages"]:
        rects = [element["rect"] for element in page["elements"]]
        assert len(rects) == page["elementCount"]
        for rect in rects:
            assert all(rect[key] % CELL == 0 for key in ("x", "y", "w", "h")), rect
            assert rect["w"] >= 2 * CELL and rect["h"] >= 2 * CELL
            assert 0 <= rect["x"] and rect["x"] + rect["w"] <= params.VIEWPORT_PX[0]
            assert 0 <= rect["y"] and rect["y"] + rect["h"] <= params.VIEWPORT_PX[1]
        for a, b in combinations(rects, 2):
            assert gap_px(a, b) >= 0, (page["id"], a, b)
        # all elements fit one 85 x 64-cell window
        span_w = max(r["x"] + r["w"] for r in rects) - min(r["x"] for r in rects)
        span_h = max(r["y"] + r["h"] for r in rects) - min(r["y"] for r in rects)
        assert span_w <= WINDOW_W_PX and span_h <= WINDOW_H_PX
        for rect in rects:
            assert gap_px(rect, page["region"]) < 0 and page["region"]["w"] == WINDOW_W_PX

        cells = sum((r["w"] // CELL) * (r["h"] // CELL) for r in rects)
        wanted = round(page["areaFraction"] * WINDOW_CELLS)
        assert cells == page["totalCells"]
        if page["elementCount"] >= 2:
            assert cells == wanted
        else:
            # A single rectangle cannot be 1306 (= 2 x 653) or 1414 (= 2 x 7 x 101) cells
            # inside 85 x 64: the generator takes the nearest legal count and says so.
            assert cells == geometry.nearest_rectangle_cells(wanted)
            assert abs(cells - wanted) <= 2
        assert page["areaFractionActual"] == pytest.approx(cells / WINDOW_CELLS, abs=1e-6)


def test_distractors_and_text_keep_clear_of_the_flashing_elements(generated):
    _, _, truth = generated
    for page in truth["pages"]:
        rects = [element["rect"] for element in page["elements"]]
        placed = page["layout"]["distractorRects"]
        assert set(placed) == set(page["distractors"])
        items = list(placed.values()) + ([page["layout"]["text"]] if page["layout"]["text"] else [])
        for item in items:
            for rect in rects:
                assert gap_px(item, rect) >= 8, (page["id"], item, rect)
        for a, b in combinations(items, 2):
            assert gap_px(a, b) >= 0, (page["id"], a, b)
        if "marquee" in placed:
            # a whole window away: no 341 x 256 window can hold both marquee and flash cells
            marquee = placed["marquee"]
            for rect in rects:
                horizontal = max(rect["x"] - (marquee["x"] + marquee["w"]), marquee["x"] - (rect["x"] + rect["w"]))
                vertical = max(rect["y"] - (marquee["y"] + marquee["h"]), marquee["y"] - (rect["y"] + rect["h"]))
                assert horizontal >= WINDOW_W_PX or vertical >= WINDOW_H_PX, (page["id"], marquee, rect)


def test_split_cells_is_exact_for_every_grid_combination():
    import random
    region = geometry.Box(0, 0, *params.WINDOW_CELLS)
    for area in params.AREA_FRACTIONS:
        for count in params.ELEMENT_COUNTS:
            for seed in range(5):
                rng = random.Random(seed)
                boxes, total = geometry.layout_flashing_elements(rng, params.target_cells(area), count, region)
                assert len(boxes) == count
                assert all(box.w >= 2 and box.h >= 2 and region.contains(box) for box in boxes)
                if count >= 2:
                    assert total == params.target_cells(area)
    assert geometry.nearest_rectangle_cells(1306) == 1305
    assert geometry.nearest_rectangle_cells(1414) == 1416
    assert geometry.nearest_rectangle_cells(3264) == 3264


# 3. Colours ----------------------------------------------------------------------

def test_colours_follow_the_wcag_formula(generated):
    _, _, truth = generated
    assert colors.relative_luminance((0, 0, 0)) == 0
    assert colors.relative_luminance((255, 255, 255)) == pytest.approx(1.0)
    assert colors.relative_luminance((255, 0, 0)) == pytest.approx(0.2126)
    for page in truth["pages"]:
        a, b = page["colors"]["a"], page["colors"]["b"]
        luminance_a, luminance_b = colors.relative_luminance(a), colors.relative_luminance(b)
        assert page["deltaLActual"] == pytest.approx(luminance_b - luminance_a, abs=1e-6)
        assert page["luminance"]["a"] == pytest.approx(luminance_a, abs=1e-6)
        assert page["luminance"]["b"] == pytest.approx(luminance_b, abs=1e-6)
        assert abs(page["deltaLActual"] - page["deltaLTarget"]) < 0.01
        assert luminance_a < 0.80 and luminance_b > luminance_a
        assert b[0] == b[1] == b[2]
        if page["red"]:
            assert a == [255, 0, 0]
        else:
            assert a[0] == a[1] == a[2]


def test_grey_for_luminance_round_trips():
    for target in (0.0, 0.05, 0.2126, 0.4, 0.8, 1.0):
        grey = colors.grey_for_luminance(target)
        assert grey[0] == grey[1] == grey[2]
        assert abs(colors.relative_luminance(grey) - target) < 0.005


# 4. Video pages -------------------------------------------------------------------

VIDEO_PARAMS = PageParams(index=1, rate_hz=4, area_fraction=0.40, delta_l_target=0.30,
                          base_luminance=0.20, red=False, element_count=2, mechanism="video",
                          distractors=("fade",), region_x=24, region_y=16, background=(255, 255, 255))


def test_video_page_is_skipped_cleanly_without_ffmpeg(tmp_path, monkeypatch):
    monkeypatch.setattr(generator, "sample_page", lambda rng, index: VIDEO_PARAMS)
    out_dir = tmp_path / "pages"
    out_dir.mkdir()
    entry = generator.build_page(SEED, 1, out_dir, tmp_path, ffmpeg=None)
    assert entry["mechanism"] == "video-skipped"
    assert entry["expected"]["verdict"] == "SKIP"
    assert entry["videos"] == [] and entry["culprits"] == []
    html = (out_dir / "bench-0001.html").read_text(encoding="utf-8")
    assert "Video unavailable" in html and "<video" not in html
    assert not list(out_dir.glob("*.webm"))


def test_video_page_writes_webm_files_when_ffmpeg_exists(tmp_path, monkeypatch):
    ffmpeg = ffmpeg_tools.find_ffmpeg()
    if ffmpeg is None:
        pytest.skip("ffmpeg not available; video pages are skipped (documented behaviour)")
    monkeypatch.setattr(generator, "sample_page", lambda rng, index: VIDEO_PARAMS)
    out_dir = tmp_path / "pages"
    out_dir.mkdir()
    entry = generator.build_page(SEED, 1, out_dir, tmp_path, ffmpeg=ffmpeg)
    assert entry["mechanism"] == "video" and entry["expected"]["verdict"] == "FAIL"
    assert len(entry["videos"]) == 2
    for relative in entry["videos"]:
        path = tmp_path / relative
        assert path.stat().st_size > 0
        assert path.read_bytes()[:4] == b"\x1a\x45\xdf\xa3"       # EBML header: a WebM/Matroska file
    html = (out_dir / "bench-0001.html").read_text(encoding="utf-8")
    assert html.count("<video ") == 2
    assert "autoplay muted loop playsinline" in html


def test_frame_state_matches_floor_formula():
    # 4 Hz at 60 fps: 7.5 frames per half cycle -> frames 0-7 A, 8-14 B, 15-22 A ...
    states = [ffmpeg_tools.frame_state(i, 4) for i in range(24)]
    assert states == [0] * 8 + [1] * 7 + [0] * 8 + [1]
    # 3.5 Hz: 2 * hz = 7, so floor(i * 7 / 60) % 2
    assert [ffmpeg_tools.frame_state(i, 3.5) for i in range(10)] == [0] * 9 + [1]
    assert ffmpeg_tools.even(7) == 8 and ffmpeg_tools.even(8) == 8


# 5. Boundary and verdict rules on hand-picked parameters -----------------------------

def outcome(**overrides):
    base = dict(rate_hz=4, total_cells=2176, delta_l_actual=0.30, red=False, area_fraction=0.40,
                area_fraction_actual=0.40, delta_l_target=0.30, mechanism="css")
    base.update(overrides)
    return expected_outcome(**base)


def test_boundary_rules():
    at_limit = outcome(rate_hz=3)
    assert at_limit["verdict"] == "PASS" and at_limit["advisory"] is True
    assert at_limit["boundary"] and "rate 3 Hz" in at_limit["boundaryReasons"][0]

    just_over = outcome(area_fraction=0.26, area_fraction_actual=1416 / WINDOW_CELLS, total_cells=1416)
    assert just_over["verdict"] == "FAIL" and just_over["boundaryReasons"] == [
        "area fraction 0.26 is within 0.0125 of the 25% limit"]

    just_under = outcome(area_fraction=0.24, area_fraction_actual=1305 / WINDOW_CELLS, total_cells=1305)
    assert just_under["verdict"] == "PASS" and just_under["boundary"]

    near_delta = outcome(delta_l_actual=0.1049, delta_l_target=0.11)
    assert near_delta["boundary"] and "delta L" in near_delta["boundaryReasons"][0]

    video_small_step = outcome(mechanism="video", delta_l_actual=0.109, delta_l_target=0.11)
    assert any(reason.startswith("video: lossy") for reason in video_small_step["boundaryReasons"])

    video_near_area = outcome(mechanism="video", area_fraction=0.26,
                              area_fraction_actual=1416 / WINDOW_CELLS, total_cells=1416)
    assert len(video_near_area["boundaryReasons"]) == 2
    assert any("chroma" in reason for reason in video_near_area["boundaryReasons"])

    clear = outcome(rate_hz=3.5)
    assert clear["verdict"] == "FAIL" and clear["boundary"] is False and clear["boundaryReasons"] == []
    assert outcome(mechanism="video")["boundary"] is False


def test_verdict_rules():
    assert outcome()["types"] == ["general"]
    red_only = outcome(red=True, delta_l_actual=0.0497, delta_l_target=0.05)
    assert red_only["verdict"] == "FAIL" and red_only["types"] == ["red"]
    both = outcome(red=True, delta_l_actual=0.60, delta_l_target=0.60)
    assert both["types"] == ["general", "red"]
    assert outcome(delta_l_actual=0.0916, delta_l_target=0.09)["verdict"] == "PASS"
    assert outcome(total_cells=1360)["verdict"] == "PASS"            # exactly 25% passes
    assert outcome(total_cells=1361)["verdict"] == "FAIL"            # more than 25% fails
    slow = outcome(rate_hz=2, red=True)
    assert slow["verdict"] == "PASS" and slow["advisory"] is True
    assert outcome(rate_hz=1)["advisory"] is False
    small_and_slow = outcome(rate_hz=2, total_cells=272)
    assert small_and_slow["advisory"] is False


# 6. CLI ----------------------------------------------------------------------------

def test_cli_prints_a_summary_line(tmp_path):
    out_dir = tmp_path / "pages"
    run = subprocess.run([sys.executable, "-m", "bench.generate", "--n", "2", "--seed", "1",
                          "--out", str(out_dir)], capture_output=True, text=True,
                         cwd=Path(__file__).resolve().parents[2])
    assert run.returncode == 0, run.stderr
    assert run.stdout.startswith("wrote 2 pages to")
    assert "mechanisms:" in run.stdout and "expected:" in run.stdout
    assert (tmp_path / "truth.json").is_file()
    assert sorted(path.name for path in out_dir.glob("*.html")) == ["bench-0001.html", "bench-0002.html"]
