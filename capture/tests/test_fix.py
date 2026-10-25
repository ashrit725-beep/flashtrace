"""Tests for capture/fix.py: fix maths, fix proposals and verification by re-capture (SPEC 7, M6).

Pure tests check every formula against numbers worked out by hand.  The proposal tests build small
synthetic captures (frames written to a job's frames/ folder and analysed by the engine) and hand-made
culprit dicts in the report schema's shape, so they need no browser.  Browser tests (``-m browser``)
audit capture/pages/css_keyframes.html and verify the proposed fixes by re-capturing it.
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from capture.fix import (
    ElementAnimations,
    area_limit_cells,
    area_scale_factor,
    companion_fixes,
    contrast_srgb,
    css_list,
    css_property_name,
    culprits_to_fix,
    encoded_to_rgb8,
    find_contrast,
    find_opacity_squeeze,
    find_saturate,
    flashing_animation,
    keyframe_colours,
    keyframes_css,
    lowered_colours,
    luminance8,
    moves_by_itself,
    propose_fixes,
    rate_fix_duration,
    red_replacement,
    redness8,
    safe_selector,
    saturate_srgb,
    scale_to_luminance,
    squeezed_pair,
    unfixed_note,
    verify_fix,
)
from capture.frames import FrameSequence
from cli.jobs import FRAMES_DIR_NAME, AuditSettings, new_job_dir
from cli.report import build_report, validate_report
from engine import AnalysisResult, analyze
from engine.color import srgb8_to_linear, uv_chromaticity

REPO_ROOT = Path(__file__).resolve().parents[2]
PAGES = REPO_ROOT / "capture" / "pages"
WIDTH, HEIGHT, FPS = 1024, 256, 60
BOX = {"x": 100.0, "y": 0.0, "w": 400.0, "h": 256.0}   # 100 x 64 cells = 6 400 cells
CAPTURE_OK = {"strategy": "waapi_step", "deterministic": True, "frameCount": 2 * FPS}
BLACK, WHITE, RED, GREY = (0, 0, 0), (255, 255, 255), (255, 0, 0), (127, 127, 127)


def uv(rgb8: tuple[int, int, int]) -> np.ndarray:
    return uv_chromaticity(srgb8_to_linear(np.array(rgb8, dtype=np.uint8)))


# --------------------------------------------------------------------------------------- pure maths
def test_rate_fix_duration_arithmetic() -> None:
    # D' = ceil(D x rate / 2.5): the iteration is stretched by the factor rate / 2.5.
    assert rate_fix_duration(4, 250) == 400     # x 1.6
    assert rate_fix_duration(5, 250) == 500     # x 2.0
    assert rate_fix_duration(6, 200) == 480     # x 2.4 (the demo loader's 0.2 s cycle, counted 6 in its busiest second)
    assert rate_fix_duration(4.5, 250) == 450   # x 1.8
    assert rate_fix_duration(4, 1000) == 1600   # x 1.6
    assert rate_fix_duration(7, 333) == 933     # x 2.8 -> 932.4 -> 933 ms (rounded up)


def test_stretched_square_waves_count_at_most_three() -> None:
    # The rate fix's promise in the engine's own terms: a strobe whose busiest second the engine counts
    # as N flashes, stretched by N / 2.5, is counted at most 3 (WCAG fails only "more than three").
    for period_ms in (200, 250):
        before = analyze(full_frame_strobe(period_ms), fps=FPS, viewport=(WIDTH, HEIGHT))
        stretched = rate_fix_duration(before.general_max_rate, period_ms)
        after = analyze(full_frame_strobe(stretched), fps=FPS, viewport=(WIDTH, HEIGHT))
        assert before.general_max_rate >= 4 and after.general_max_rate <= 3 and after.verdict == "PASS"


@pytest.mark.parametrize("colour", [WHITE, RED, (200, 100, 50), (30, 200, 90), (90, 60, 240)])
def test_luminance_search_keeps_hue_and_reaches_target(colour: tuple[int, int, int]) -> None:
    lowered = lowered_colours([BLACK, colour])[colour]
    gap = luminance8(lowered) - luminance8(BLACK)
    assert 0.08 <= gap <= 0.09  # at most the 0.09 target, and not far below it
    # Same chromaticity (hue and saturation) up to the rounding of whole 0-255 values.
    assert float(np.linalg.norm(uv(lowered) - uv(colour))) < 0.01


def test_lowered_colours_brings_every_pair_within_the_target() -> None:
    colours = [(20, 20, 20), WHITE, (180, 40, 40)]
    final = [lowered_colours(colours).get(c, c) for c in colours]
    values = [luminance8(c) for c in final]
    assert max(values) - min(values) <= 0.09
    assert lowered_colours([BLACK, (40, 40, 40)]) == {}  # already within 0.09: nothing changes
    assert scale_to_luminance(WHITE, 0.09) == (84, 84, 84)


def test_contrast_matches_filter_effects_by_hand() -> None:
    # contrast(a): C' = a x C + (0.5 - 0.5 a), clamped to 0..1.
    assert contrast_srgb(np.ones(3), 0.5).tolist() == [0.75, 0.75, 0.75]
    assert contrast_srgb(np.zeros(3), 0.5).tolist() == [0.25, 0.25, 0.25]
    assert contrast_srgb(np.array([0.6]), 2.0)[0] == pytest.approx(0.7)
    assert contrast_srgb(np.array([0.9]), 2.0)[0] == 1.0      # 1.3 clamped
    assert contrast_srgb(np.array([0.2, 0.4]), 1.0).tolist() == [0.2, 0.4]


def test_saturate_matches_filter_effects_by_hand() -> None:
    # saturate(0) of pure red is the first column of the luminance row: 0.213 everywhere.
    assert saturate_srgb(np.array([1.0, 0.0, 0.0]), 0.0) == pytest.approx([0.213, 0.213, 0.213])
    # saturate(0.5): R' = 0.213 + 0.787 x 0.5 = 0.6065, G' = B' = 0.213 - 0.213 x 0.5 = 0.1065.
    assert saturate_srgb(np.array([1.0, 0.0, 0.0]), 0.5) == pytest.approx([0.6065, 0.1065, 0.1065])
    assert saturate_srgb(np.array([0.0, 1.0, 0.0]), 0.0) == pytest.approx([0.715, 0.715, 0.715])
    colour = np.array([0.3, 0.6, 0.9])
    assert saturate_srgb(colour, 1.0) == pytest.approx(colour)  # saturate(1) changes nothing


def test_contrast_search_reaches_target() -> None:
    amount = find_contrast(WHITE, BLACK)
    assert amount is not None and 0 < amount < 1

    def gap(a: float) -> float:
        light = tuple(int(v) for v in np.floor(contrast_srgb(np.ones(3), a) * 255 + 0.5))
        dark = tuple(int(v) for v in np.floor(contrast_srgb(np.zeros(3), a) * 255 + 0.5))
        return luminance8(light) - luminance8(dark)  # type: ignore[arg-type]

    assert gap(amount) <= 0.09 and gap(amount + 0.01) > 0.09
    assert find_contrast((100, 100, 100), (110, 110, 110)) is None  # already under the target


def test_saturate_search_brings_red_below_08() -> None:
    amount = find_saturate(RED)
    assert amount is not None and 0 < amount < 1

    def ratio(a: float) -> float:
        out = tuple(int(v) for v in np.floor(saturate_srgb(np.array([1.0, 0.0, 0.0]), a) * 255 + 0.5))
        return redness8(out)  # type: ignore[arg-type]

    assert ratio(amount) < 0.8 and ratio(amount + 0.01) >= 0.8
    assert find_saturate((200, 120, 120)) is None  # ratio 0.45: nothing to do


@pytest.mark.parametrize("colour", [RED, (204, 51, 0), (240, 30, 30), (180, 0, 20)])
def test_red_replacement_keeps_luminance_and_hits_075(colour: tuple[int, int, int]) -> None:
    new = red_replacement(colour)
    assert 0.70 <= redness8(new) <= 0.75
    assert abs(luminance8(new) - luminance8(colour)) < 0.005


def test_area_factor() -> None:
    assert area_limit_cells(1024) == 1360                      # 25% of 85 x 64 cells
    assert area_scale_factor(7500, 1360) == pytest.approx(math.sqrt(1360 / 7500) * 0.95)
    assert area_scale_factor(1360, 1360) is None               # exactly 25% does not qualify
    factor = area_scale_factor(6400, 1360)
    assert factor is not None and 6400 * factor**2 < 1360


def test_keyframes_css_from_inspected_keyframes() -> None:
    keyframes = [{"offset": 0, "easing": "steps(1)", "backgroundColor": "rgb(0, 0, 0)"},
                 {"offset": 0.5, "easing": "steps(1)", "backgroundColor": "rgb(255, 255, 255)"},
                 {"offset": 1, "easing": "steps(1)", "backgroundColor": "rgb(0, 0, 0)"}]
    assert keyframe_colours(keyframes) == [(BLACK, 1.0), (WHITE, 1.0)]
    css = keyframes_css("ft-fix-1-lum", keyframes, {WHITE: (84, 84, 84)})
    assert css == ("@keyframes ft-fix-1-lum { 0% { background-color: rgb(0, 0, 0); animation-timing-function: steps(1); } "
                   "50% { background-color: #545454; animation-timing-function: steps(1); } "
                   "100% { background-color: rgb(0, 0, 0); animation-timing-function: steps(1); } }")
    assert css_property_name("backgroundColor") == "background-color" and css_property_name("cssFloat") == "float"
    hostile = [{"offset": 0, "easing": "linear", "content": "'}'; body { display: none"}]
    assert keyframes_css("x", hostile, {}) is None
    assert safe_selector("#box > .a") and not safe_selector("a } body {") and not safe_selector("")


# --------------------------------------------------------------------------------------- synthetic jobs
def full_frame_strobe(period_ms: int, seconds: int = 3) -> list[np.ndarray]:
    """A full-frame black/white strobe: black for the first half of each period, white for the second."""
    black, white = np.zeros((HEIGHT, WIDTH, 3), dtype=np.uint8), np.full((HEIGHT, WIDTH, 3), 255, dtype=np.uint8)
    return [white if (i * 1000 * 2 // FPS // period_ms) % 2 else black for i in range(seconds * FPS)]


def box_frames(on: tuple[int, int, int], off: tuple[int, int, int], hz: int = 4,
               page: tuple[int, int, int] = WHITE) -> list[np.ndarray]:
    """2 s of a ``page``-coloured page whose BOX switches between ``off`` and ``on`` at ``hz`` (integer state formula)."""
    frames = []
    for i in range(2 * FPS):
        frame = np.full((HEIGHT, WIDTH, 3), page, dtype=np.uint8)
        frame[:, 100:500] = on if (i * 2 * hz // FPS) % 2 else off
        frames.append(frame)
    return frames


def synthetic_job(base: Path, frames: list[np.ndarray]) -> tuple[Path, AnalysisResult, AuditSettings]:
    settings = AuditSettings(duration_s=2.0, viewport=(WIDTH, HEIGHT), fps=FPS, attribute="bbox")
    job_dir = new_job_dir(base, "file:///synthetic.html", settings)
    sequence = FrameSequence.write_arrays(frames, job_dir / FRAMES_DIR_NAME)
    return job_dir, analyze(sequence, fps=FPS, viewport=(WIDTH, HEIGHT)), settings


def culprit(rank: int, selector: str, animation: dict[str, Any] | None, overlap: float = 0.9,
            rect: dict[str, float] | None = None, delta: float | None = None, minimal: bool = False) -> dict[str, Any]:
    return {"rank": rank, "selector": selector, "nodeType": "div", "ftId": None, "rect": dict(rect or BOX),
            "animation": animation, "overlap": overlap, "ablationDelta": delta, "inMinimalSet": minimal}


def css_animation(on: tuple[int, int, int], off: tuple[int, int, int], name: str = "blink") -> dict[str, Any]:
    keyframes = [{"offset": 0, "easing": "steps(1)", "backgroundColor": f"rgb{off}"},
                 {"offset": 0.5, "easing": "steps(1)", "backgroundColor": f"rgb{on}"},
                 {"offset": 1, "easing": "steps(1)", "backgroundColor": f"rgb{off}"}]
    return {"name": name, "duration": 250, "keyframes": keyframes}


@pytest.fixture(scope="module")
def general_job(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, AnalysisResult, AuditSettings]:
    """Black <-> white box at 4 Hz: a general violation at 4 flashes per second."""
    return synthetic_job(tmp_path_factory.mktemp("fix-general"), box_frames(WHITE, BLACK))


@pytest.fixture(scope="module")
def red_job(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, AnalysisResult, AuditSettings]:
    """Red <-> equal-luminance grey at 4 Hz: a red violation only (the luminance change is under 0.10)."""
    return synthetic_job(tmp_path_factory.mktemp("fix-red"), box_frames(RED, GREY))


def test_css_animation_culprit_gets_rate_luminance_area(general_job: tuple[Path, AnalysisResult, AuditSettings]) -> None:
    job_dir, analysis, settings = general_job
    assert analysis.verdict == "FAIL" and analysis.general_max_rate == 4
    fixes = propose_fixes([culprit(1, "#box", css_animation(WHITE, BLACK))], analysis, settings, job_dir,
                          capture=CAPTURE_OK)
    assert [f["kind"] for f in fixes] == ["rate", "luminance", "area"]
    rate, luminance, area = fixes
    assert rate["css"] == "#box { animation-duration: 400ms !important; }"
    assert "counted up to 4 flashes in one second" in rate["rationale"] and "from 250 ms to 400 ms" in rate["rationale"]
    # The peak one-second count is not an average: no "flashes per iteration" figure, no 2.5/s promise.
    assert "flash(es)" not in rate["rationale"] and "at most 2.5" not in rate["rationale"]
    assert "busiest one-second window" in rate["rationale"] and "at most 3" in rate["rationale"]
    assert luminance["css"].startswith("@keyframes ft-fix-1-lum {") and "#545454" in luminance["css"]
    assert luminance["css"].endswith("#box { animation-name: ft-fix-1-lum !important; }")
    assert "L 1.000" in luminance["rationale"] and "L 0.000" in luminance["rationale"]  # measured states
    assert area["css"] == ("/* not auto-applied: changes layout */ #box { transform: scale(0.437); "
                           "transform-origin: top left; }")  # sqrt(1360 / 6400) x 0.95 = 0.4379
    for fix in fixes:
        assert fix["culprit"] == 1 and fix["after"] is None and fix["verified"] is False
        assert fix["before"] == {"verdict": "FAIL", "generalMaxRatePerSec": 4, "redMaxRatePerSec": 0,
                                 "qualifyingGeneral": len(analysis.qualifying_general), "qualifyingRed": 0,
                                 "deterministic": True}


def test_script_driven_culprit_gets_filter_and_says_why_no_rate(
        general_job: tuple[Path, AnalysisResult, AuditSettings]) -> None:
    job_dir, analysis, settings = general_job
    fixes = propose_fixes([culprit(1, "#canvas", None)], analysis, settings, job_dir, capture=CAPTURE_OK)
    assert [f["kind"] for f in fixes] == ["luminance", "area"]
    assert fixes[0]["css"].startswith("#canvas { filter: contrast(0.0") and "No rate fix" in fixes[0]["rationale"]


def test_red_violation_gets_red_fix_not_luminance(red_job: tuple[Path, AnalysisResult, AuditSettings]) -> None:
    job_dir, analysis, settings = red_job
    assert [v.type for v in analysis.violations] == ["red"]
    video = dict(culprit(2, "video", None, overlap=0.8), nodeType="video")
    fixes = propose_fixes([culprit(1, "#badge", css_animation(RED, GREY)), video],
                          analysis, settings, job_dir, capture=CAPTURE_OK)
    assert [(f["culprit"], f["kind"]) for f in fixes] == [(1, "rate"), (1, "red"), (1, "area"), (2, "red"), (2, "area")]
    keyframe_red, filter_red = fixes[1], fixes[3]
    assert "@keyframes ft-fix-1-red" in keyframe_red["css"] and "#f62929" in keyframe_red["css"]
    assert filter_red["css"].startswith("video { filter: saturate(0.")


def test_which_culprits_get_fixes() -> None:
    animation = css_animation(WHITE, BLACK)
    # bbox: overlap >= 0.5, and of those the elements that move by themselves (animation, video, canvas...).
    tied = [culprit(2, "svg.court", None, overlap=0.92), culprit(1, "#loader", animation, overlap=0.92),
            dict(culprit(3, "video", None, overlap=0.6), nodeType="video"), culprit(4, "main", animation, overlap=0.2)]
    assert [c["selector"] for c in culprits_to_fix(tied)] == ["#loader", "video"]
    # Nothing moves by itself (script-driven boxes): only the top-ranked eligible culprit.
    still = [culprit(2, "#b", None, overlap=0.7), culprit(1, "#a", None, overlap=0.9), culprit(3, "main", None, overlap=0.2)]
    assert [c["selector"] for c in culprits_to_fix(still)] == ["#a"]
    # After ablation: the minimal set, whatever the overlap.
    ablated = [culprit(1, "#a", animation, overlap=0.9, delta=0.0), culprit(2, "#b", None, overlap=0.3, delta=4.0, minimal=True)]
    assert [c["selector"] for c in culprits_to_fix(ablated)] == ["#b"]


def test_fixes_are_schema_valid_in_a_report(general_job: tuple[Path, AnalysisResult, AuditSettings]) -> None:
    from cli.tests.helpers import FakeCaptureMeta

    job_dir, analysis, settings = general_job
    culprits = [culprit(1, "#box", css_animation(WHITE, BLACK))]
    fixes = propose_fixes(culprits, analysis, settings, job_dir, capture=CAPTURE_OK)
    report = build_report("file:///synthetic.html", settings, FakeCaptureMeta(frame_count=analysis.frame_count),
                          analysis, job_dir, culprits=culprits, fixes=fixes, captured_at="2026-10-24T12:00:00Z")
    validate_report(report)
    # An area fix is never applied: verify_fix returns at once, without a browser.
    area_index = [f["kind"] for f in fixes].index("area")
    checked = verify_fix(report, area_index, job_dir, report["url"], settings)
    assert checked["fixes"][area_index]["verified"] is False and checked["fixes"][area_index]["after"] is None
    assert "never verifies it by re-capture" in checked["fixes"][area_index]["rationale"]
    validate_report(checked)
    with pytest.raises(IndexError):
        verify_fix(report, len(fixes), job_dir, report["url"], settings)


# --------------------------------------------------------------------------------------- an element with several animations
GLOW_KEYFRAMES = [{"offset": 0, "easing": "linear", "color": "rgb(255, 255, 255)"},
                  {"offset": 0.5, "easing": "linear", "color": "rgb(200, 200, 200)"},
                  {"offset": 1, "easing": "linear", "color": "rgb(255, 255, 255)"}]


def glow(duration_ms: float) -> dict[str, Any]:
    """A text-colour animation: one change each way per iteration, so one flash per iteration at most."""
    return {"name": "glow", "kind": "css", "duration": duration_ms, "keyframes": GLOW_KEYFRAMES}


def two_animations(glow_ms: float) -> ElementAnimations:
    """What the page read reports for '#box { animation: glow <glow_ms> linear infinite, strobe 250ms ... }'."""
    strobe = dict(css_animation(BLACK, WHITE, name="strobe"), kind="css")
    return ElementAnimations(names=("glow", "strobe"), durations=(f"{glow_ms / 1000:g}s", "0.25s"),
                             animations=(glow(glow_ms), strobe))


@pytest.mark.parametrize("glow_ms", [3000.0, 100.0])
def test_the_animation_that_matches_the_rate_is_chosen(glow_ms: float) -> None:
    # glow: 2 colour changes per iteration -> 1 flash per 3 s (0.33/s) or per 0.1 s (10/s);
    # strobe: 2 changes per 250 ms -> 4/s, exactly the measured rate.
    chosen = flashing_animation(two_animations(glow_ms).animations, 4)
    assert chosen is not None and chosen["name"] == "strobe"
    spin = {"name": "spin", "kind": "css", "duration": 2000, "keyframes": [{"offset": 1, "transform": "rotate(360deg)"}]}
    assert flashing_animation([spin], 4) is spin  # nothing changes paint: the first animation stays
    fade = {"name": "transition: opacity", "kind": "transition", "duration": 300, "keyframes": []}
    assert flashing_animation([fade], 4) is None


@pytest.mark.parametrize("glow_ms", [3000.0, 100.0])
def test_fixes_change_only_the_flashing_animation(general_job: tuple[Path, AnalysisResult, AuditSettings],
                                                   glow_ms: float) -> None:
    job_dir, analysis, settings = general_job
    # The culprit record keeps only the first animation the page listed (glow); the page read lists both.
    fixes = propose_fixes([culprit(1, "#box", glow(glow_ms))], analysis, settings, job_dir, capture=CAPTURE_OK,
                          animations={"#box": two_animations(glow_ms)})
    assert [f["kind"] for f in fixes] == ["rate", "luminance", "area"]
    rate, luminance, _area = fixes
    # Durations in animation-name order; only strobe's entry changes (250 ms x 4 / 2.5 = 400 ms).
    assert rate["css"] == f"#box {{ animation-duration: {glow_ms / 1000:g}s, 400ms !important; }}"
    assert "'strobe' from 250 ms to 400 ms" in rate["rationale"] and "'glow'" not in rate["rationale"]
    assert "only it is changed" in rate["rationale"]
    # The new keyframes copy strobe's background colours, and glow keeps running.
    assert luminance["css"].endswith("#box { animation-name: glow, ft-fix-1-lum !important; }")
    assert "background-color: #545454" in luminance["css"] and "rgb(200, 200, 200)" not in luminance["css"]


def test_css_list_repeats_like_css() -> None:
    assert css_list(("1s",), 1, "400ms", 3) == "1s, 400ms, 1s"
    assert css_list(("glow", "strobe"), 0, "ft-fix-1-lum", 2) == "ft-fix-1-lum, strobe"
    assert css_list(("1s; color: red",), 1, "400ms", 2) is None and css_list((), 0, "x", 1) is None


def test_unread_animation_list_is_said(general_job: tuple[Path, AnalysisResult, AuditSettings]) -> None:
    job_dir, analysis, settings = general_job
    fixes = propose_fixes([culprit(1, "#box", css_animation(WHITE, BLACK))], analysis, settings, job_dir,
                          capture=CAPTURE_OK, animations={})
    assert fixes[0]["css"] == "#box { animation-duration: 400ms !important; }"
    assert "could not read this element's list of animations" in fixes[0]["rationale"]


def test_script_animation_gets_no_css_rate_fix(general_job: tuple[Path, AnalysisResult, AuditSettings]) -> None:
    job_dir, analysis, settings = general_job
    animate = dict(css_animation(WHITE, BLACK, name="(unnamed Web Animation)"), kind="script")
    element = ElementAnimations(names=("none",), durations=("0s",), animations=(animate,))
    fixes = propose_fixes([culprit(1, "#box", animate)], analysis, settings, job_dir, capture=CAPTURE_OK,
                          animations={"#box": element})
    assert [f["kind"] for f in fixes] == ["luminance", "area"]
    assert fixes[0]["css"].startswith("#box { filter: contrast(") and "started by script" in fixes[0]["rationale"]


# --------------------------------------------------------------------------------------- see-through states
def opacity_blink(*values: str) -> dict[str, Any]:
    keyframes = [{"offset": offset, "easing": "steps(1)", "opacity": value}
                 for offset, value in zip((0, 0.5, 1), (values[0], values[1], values[0]))]
    return {"name": "blink", "duration": 250, "keyframes": keyframes}


@pytest.fixture(scope="module")
def blink_job(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, AnalysisResult, AuditSettings]:
    """A white box blinking opacity 1 <-> 0 at 4 Hz over a black page: one state is the page itself."""
    return synthetic_job(tmp_path_factory.mktemp("fix-blink"), box_frames(WHITE, BLACK, page=BLACK))


def test_opacity_blink_gets_an_opacity_fix_not_a_filter(blink_job: tuple[Path, AnalysisResult, AuditSettings]) -> None:
    job_dir, analysis, settings = blink_job
    fixes = propose_fixes([culprit(1, "#blink", opacity_blink("1", "0"))], analysis, settings, job_dir,
                          capture=CAPTURE_OK)
    assert [f["kind"] for f in fixes] == ["rate", "luminance", "area"]
    luminance = fixes[1]
    assert "filter" not in luminance["css"] and luminance["css"].startswith("@keyframes ft-fix-1-lum {")
    assert luminance["css"].endswith("#blink { animation-name: ft-fix-1-lum !important; }")
    # Composite of white over black at opacity o is o (sRGB values): the new range differs by <= 0.09 L.
    low, high = sorted(float(v) for v in set(re.findall(r"opacity: ([0-9.]+)", luminance["css"])))
    over_black = [encoded_to_rgb8(np.full(3, value)) for value in (low, high)]
    assert 0 < luminance8(over_black[1]) - luminance8(over_black[0]) <= 0.09
    assert "pulls both measured states toward their middle colour" in luminance["rationale"]


def test_visibility_blink_gets_no_filter_and_says_why(blink_job: tuple[Path, AnalysisResult, AuditSettings]) -> None:
    job_dir, analysis, settings = blink_job
    hidden = {"name": "hide", "duration": 250, "keyframes": [
        {"offset": 0, "easing": "steps(1)", "visibility": "visible"},
        {"offset": 0.5, "easing": "steps(1)", "visibility": "hidden"},
        {"offset": 1, "easing": "steps(1)", "visibility": "visible"}]}
    fixes = propose_fixes([culprit(1, "#blink", hidden)], analysis, settings, job_dir, capture=CAPTURE_OK)
    assert [f["kind"] for f in fixes] == ["rate", "area"]
    assert "No filter fix" in fixes[0]["rationale"] and "shows through" in fixes[0]["rationale"]


def test_opacity_squeeze_maths() -> None:
    # Pulling black and white toward mid-grey: the gap shrinks with the factor and meets 0.09 at the answer.
    factor = find_opacity_squeeze(BLACK, WHITE)
    assert factor is not None and 0 < factor < 1
    first, second = squeezed_pair(BLACK, WHITE, factor)
    assert luminance8(second) - luminance8(first) <= 0.09
    first, second = squeezed_pair(BLACK, WHITE, factor + 0.01)
    assert luminance8(second) - luminance8(first) > 0.09
    assert squeezed_pair(BLACK, WHITE, 1.0) == (BLACK, WHITE) and find_opacity_squeeze(BLACK, (40, 40, 40)) is None


# --------------------------------------------------------------------------------------- which culprits, and why none
def test_ablation_without_a_minimal_set_falls_back_to_overlap(general_job: tuple[Path, AnalysisResult, AuditSettings]) -> None:
    job_dir, analysis, settings = general_job
    spin = {"name": "spin", "duration": 2000, "keyframes": [{"offset": 1, "transform": "rotate(360deg)"}]}
    overlay = culprit(1, "#overlay", None, overlap=0.998, delta=2.0)                      # a script-driven strobe
    spinner = culprit(2, "#spinner", spin, overlap=0.01, delta=0.0, rect={"x": 140, "y": 90, "w": 24, "h": 24})
    assert [c["selector"] for c in culprits_to_fix([overlay, spinner])] == ["#overlay"]
    fixes = propose_fixes([overlay, spinner], analysis, settings, job_dir, capture=CAPTURE_OK)
    assert fixes and {f["culprit"] for f in fixes} == {1}
    assert "Ablation found no set of culprits" in fixes[0]["rationale"] and "box overlap" in fixes[0]["rationale"]


def test_every_minimal_set_member_gets_fixes() -> None:
    animation = css_animation(WHITE, BLACK)
    members = [culprit(rank, f"#t{rank}", animation, overlap=0.2, delta=0.0, minimal=True) for rank in range(1, 6)]
    assert len(culprits_to_fix(members)) == 5  # all needed for a PASS: no bbox cap of 3


def test_a_still_image_does_not_take_the_fixes() -> None:
    banner = culprit(1, "#banner", None, overlap=0.95)                  # toggled by script, no animation
    hero = dict(culprit(2, "#hero", None, overlap=0.95), nodeType="img")  # a still picture under it
    assert [c["selector"] for c in culprits_to_fix([banner, hero])] == ["#banner"]
    assert not moves_by_itself(hero)
    assert moves_by_itself(dict(hero, nodeType="iframe (cross-origin, unattributable)"))
    assert moves_by_itself(dict(hero, nodeType="canvas"))


def test_no_fix_is_explained_when_no_culprit_qualifies() -> None:
    tiles = [culprit(rank, f"#t{rank}", None, overlap=0.25) for rank in range(1, 5)]  # four synced tiles
    assert culprits_to_fix(tiles) == []
    note = unfixed_note(tiles)
    assert note is not None and "IoU of at least 0.5" in note and "best overlap here is 0.25" in note
    assert "--attribute ablate" in note
    assert "Ablation found no minimal set either" in (unfixed_note([dict(t, ablationDelta=0.0) for t in tiles]) or "")
    assert unfixed_note([culprit(1, "#a", None, overlap=0.9)]) is None and unfixed_note([]) is None


# --------------------------------------------------------------------------------------- verifying a minimal set
def set_report() -> dict[str, Any]:
    """Three culprits, #t1 and #t2 in the minimal set, with rate and luminance fixes each (#t3 has none)."""
    culprits = [culprit(1, "#t1", None, minimal=True), culprit(2, "#t2", None, minimal=True),
                culprit(3, "#t3", None, minimal=True)]
    fixes = [{"culprit": rank, "kind": kind, "css": f"#t{rank} {{ /* {kind} */ }}", "rationale": "r.",
              "before": {}, "after": None, "verified": False}
             for rank, kind in ((1, "rate"), (1, "luminance"), (2, "luminance"), (2, "rate"), (2, "area"))]
    return {"url": "file:///set.html", "capture": {"strategy": "waapi_step"}, "culprits": culprits, "fixes": fixes}


def test_minimal_set_fixes_are_verified_together(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    report = set_report()
    assert companion_fixes(report, 0) == [3]   # #t2's fix of the same kind (rate)
    assert companion_fixes(report, 1) == [2]   # #t2's luminance fix
    injected: list[str] = []

    def fake_recapture(job_dir: Path, n: int, url: str, settings: AuditSettings, strategy: str, css: str,
                       progress: Any = None, browser: Any = None) -> tuple[dict[str, Any], None]:
        injected.append(css)
        return {"verdict": "PASS", "generalMaxRatePerSec": 3, "redMaxRatePerSec": 0, "qualifyingGeneral": 0,
                "qualifyingRed": 0, "deterministic": True}, None

    monkeypatch.setattr("capture.fix.recapture", fake_recapture)
    settings = AuditSettings(duration_s=2.0, viewport=(WIDTH, HEIGHT), fps=FPS)
    checked = verify_fix(report, 0, tmp_path, report["url"], settings)
    assert injected == ["#t1 { /* rate */ }\n#t2 { /* rate */ }"]
    rationale = checked["fixes"][0]["rationale"]
    assert "verified together with one fix per other member: culprit 2's rate fix (fix 4)" in rationale
    assert "Culprit(s) 3 have no fix that can be applied" in rationale and checked["fixes"][0]["verified"] is True
    # Outside a minimal set (or a set of one) a fix is injected alone, as before.
    alone = dict(report, culprits=[dict(c, inMinimalSet=c["rank"] == 1) for c in report["culprits"]])
    assert companion_fixes(alone, 0) == []
    verify_fix(alone, 0, tmp_path, report["url"], settings)
    assert injected[-1] == "#t1 { /* rate */ }"


# --------------------------------------------------------------------------------------- browser
@pytest.fixture(scope="module")
def keyframes_audit(tmp_path_factory: pytest.TempPathFactory, browser: Any) -> tuple[dict[str, Any], Path]:
    """css_keyframes.html audited for 3 s with bbox attribution (the 4 Hz #box and the 0.5 Hz #fade).

    The session's shared Chromium is passed in: while its Playwright runs in this thread, the sync API
    cannot start a second one here, so every in-process audit step must reuse it.
    """
    from cli.audit import run_audit

    report, job_dir, _code = run_audit(str(PAGES / "css_keyframes.html"), AuditSettings(duration_s=3.0, attribute="bbox"),
                                       work_dir=tmp_path_factory.mktemp("fix-browser"), browser=browser)
    return report, job_dir


def box_fix_index(report: dict[str, Any], kind: str) -> int:
    """Index in report["fixes"] of the ``kind`` fix proposed for the #box culprit."""
    box = next(c for c in report["culprits"] if c["selector"] == "#box")
    return next(i for i, f in enumerate(report["fixes"]) if f["culprit"] == box["rank"] and f["kind"] == kind)


@pytest.mark.browser
def test_bbox_audit_proposes_fixes_for_the_4hz_box(keyframes_audit: tuple[dict[str, Any], Path]) -> None:
    report, _job_dir = keyframes_audit
    assert report["verdict"] == "FAIL" and report["settings"]["attribute"] == "bbox"
    box = next(c for c in report["culprits"] if c["selector"] == "#box")
    kinds = [f["kind"] for f in report["fixes"] if f["culprit"] == box["rank"]]
    assert kinds == ["rate", "luminance", "area"]
    rate = report["fixes"][box_fix_index(report, "rate")]
    general_rate = max(v["maxRatePerSec"] for v in report["violations"] if v["type"] == "general")
    # The new duration follows from the measured rate and the 250 ms animation: ceil(rate x 250 / 2.5).
    assert rate["css"] == f"#box {{ animation-duration: {rate_fix_duration(general_rate, 250)}ms !important; }}"


@pytest.mark.browser
@pytest.mark.parametrize("kind", ["rate", "luminance"])
def test_box_fix_verifies_by_recapture(keyframes_audit: tuple[dict[str, Any], Path], kind: str, browser: Any) -> None:
    from cli.audit import verify_fix_in_job

    report, job_dir = keyframes_audit
    index = box_fix_index(report, kind)
    updated = verify_fix_in_job(job_dir, index, browser=browser)
    after = updated["fixes"][index]["after"]
    # With the fix the box flashes at most 2.5 times a second (rate) or not at all (luminance); the
    # page's 0.5 Hz #fade completes one flash in these 3 s.  Neither may exceed 3 in any second.
    assert after["verdict"] == "PASS", after
    assert after["generalMaxRatePerSec"] <= 3 and after["redMaxRatePerSec"] == 0 and after["deterministic"]
    assert updated["fixes"][index]["verified"] is True
    assert (job_dir / "verify" / f"fix-{index}" / "frames" / "000000.png").is_file()


TWO_ANIMATIONS_PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><style>
html, body { margin: 0; background: rgb(255, 255, 255); }
#banner { position: absolute; left: 40px; top: 40px; width: 300px; height: 200px;
          animation: glow 3s linear infinite, strobe 250ms steps(1, end) infinite; }
@keyframes glow { 0% { color: rgb(255, 255, 255); } 50% { color: rgb(200, 200, 200); } 100% { color: rgb(255, 255, 255); } }
@keyframes strobe { 0% { background-color: rgb(255, 255, 255); } 50% { background-color: rgb(0, 0, 0); }
                    100% { background-color: rgb(255, 255, 255); } }
</style></head><body><div id="banner"></div></body></html>
"""

OPACITY_BLINK_PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><style>
html, body { margin: 0; background: rgb(0, 0, 0); }
#blink { position: absolute; left: 40px; top: 40px; width: 300px; height: 200px; background: rgb(255, 255, 255);
         animation: blink 250ms steps(1, end) infinite; }
@keyframes blink { 0% { opacity: 1; } 50% { opacity: 0; } 100% { opacity: 1; } }
</style></head><body><div id="blink"></div></body></html>
"""


def audit_page(tmp_path: Path, html: str, browser: Any) -> tuple[dict[str, Any], Path]:
    """Write ``html`` to a file and audit it for 2 s with bbox attribution, reusing the session's Chromium."""
    from cli.audit import run_audit

    page = tmp_path / "page.html"
    page.write_text(html, encoding="utf-8")
    report, job_dir, _code = run_audit(str(page), AuditSettings(duration_s=2.0, attribute="bbox"),
                                       work_dir=tmp_path / "work", browser=browser)
    return report, job_dir


@pytest.mark.browser
def test_two_animation_element_fixes_change_only_the_strobe(tmp_path: Path, browser: Any) -> None:
    from capture.fix import read_element_animations
    from cli.audit import analyse_stored_frames, verify_fix_in_job

    report, job_dir = audit_page(tmp_path, TWO_ANIMATIONS_PAGE, browser)
    banner = next(c for c in report["culprits"] if c["selector"] == "#banner")
    assert banner["animation"]["name"] == "glow"  # the culprit record keeps only the first animation
    settings = AuditSettings(duration_s=2.0, attribute="bbox")
    analysis = analyse_stored_frames(job_dir, settings, report["capture"]["frameCount"])
    read = read_element_animations(report["url"], analysis, report["culprits"], settings, job_dir, browser=browser)
    assert read["#banner"].names == ("glow", "strobe") and read["#banner"].durations == ("3s", "0.25s")
    assert [a["name"] for a in read["#banner"].animations] == ["glow", "strobe"]
    fixes = {f["kind"]: (i, f) for i, f in enumerate(report["fixes"]) if f["culprit"] == banner["rank"]}
    rate_index, rate = fixes["rate"]
    general_rate = max(v["maxRatePerSec"] for v in report["violations"] if v["type"] == "general")
    assert rate["css"] == f"#banner {{ animation-duration: 3s, {rate_fix_duration(general_rate, 250)}ms !important; }}"
    lum_index, luminance = fixes["luminance"]
    assert luminance["css"].endswith("#banner { animation-name: glow, ft-fix-1-lum !important; }")
    assert "background-color" in luminance["css"] and "'strobe'" in luminance["rationale"]
    for index in (rate_index, lum_index):
        after = verify_fix_in_job(job_dir, index, browser=browser)["fixes"][index]
        assert after["verified"] is True and after["after"]["generalMaxRatePerSec"] <= 3, after


@pytest.mark.browser
def test_opacity_blink_fix_verifies(tmp_path: Path, browser: Any) -> None:
    from cli.audit import verify_fix_in_job

    report, job_dir = audit_page(tmp_path, OPACITY_BLINK_PAGE, browser)
    index, luminance = next((i, f) for i, f in enumerate(report["fixes"]) if f["kind"] == "luminance")
    assert "opacity: 0." in luminance["css"] and "filter" not in luminance["css"]
    checked = verify_fix_in_job(job_dir, index, browser=browser)["fixes"][index]
    assert checked["verified"] is True and checked["after"]["qualifyingGeneral"] == 0, checked
