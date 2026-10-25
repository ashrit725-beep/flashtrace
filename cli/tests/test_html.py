"""Tests for cli/html_report.py: self-contained, static, escaped, accessible HTML."""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

import numpy as np

from cli.html_report import block_max, render_html, shade_bins
from cli.jobs import AuditSettings
from cli.report import DISCLAIMER, build_report
from cli.tests.helpers import AnalysedJob, report_for

HOSTILE = "<img src=x onerror=alert(1)>"


class TagCounter(HTMLParser):
    """Counts start tags and collects every src / href attribute value."""

    def __init__(self) -> None:
        super().__init__()
        self.tags: dict[str, int] = {}
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags[tag] = self.tags.get(tag, 0) + 1
        self.links += [value or "" for name, value in attrs if name in ("src", "href")]


def parse(page: str) -> TagCounter:
    counter = TagCounter()
    counter.feed(page)
    return counter


def fail_page(job: AnalysedJob, **kwargs: object) -> str:
    return render_html(report_for(job), base_dir=job.job_dir, **kwargs)  # type: ignore[arg-type]


def test_disclaimer_appears_twice_and_verdict_is_a_word(fail_job: AnalysedJob) -> None:
    page = fail_page(fail_job)
    assert page.count(DISCLAIMER) == 2
    assert "<strong>FAIL</strong>" in page
    assert 'class="glyph" aria-hidden="true">✗<' in page


def test_page_is_self_contained_and_static(fail_job: AnalysedJob) -> None:
    page = fail_page(fail_job)
    tags = parse(page)
    assert "<svg" in page and tags.tags.get("svg", 0) == 2  # timeline + heatmap
    assert "<script src" not in page and tags.tags.get("script", 0) == 1
    assert "http://" not in page and "https://" not in page
    assert all(link.startswith("data:image/png;base64,") for link in tags.links)
    assert tags.tags.get("link", 0) == 0 and "@import" not in page and "url(http" not in page
    for moving in ("@keyframes", "animation:", "transition:", "<animate", "autoplay", "setInterval", "setTimeout"):
        assert moving not in page


def test_evidence_strip_has_prev_next_and_embedded_stills(fail_job: AnalysedJob) -> None:
    page = fail_page(fail_job)
    assert 'id="ev-prev"' in page and 'id="ev-next"' in page
    assert page.count("<figure>") == 3
    assert "frame 15 (t = 0.250 s) - first qualifying general flash" in page


def test_unembedded_frames_use_relative_paths(fail_job: AnalysedJob) -> None:
    page = fail_page(fail_job, embed_frames=False)
    assert 'src="evidence/000015.png"' in page
    assert "data:image/png" not in page


def test_hostile_strings_are_escaped(fail_job: AnalysedJob) -> None:
    report = report_for(fail_job)
    report["reason"] = HOSTILE
    report["url"] = 'file:///a"><script>alert(2)</script>.html'
    report["capture"]["notes"] = [HOSTILE]
    page = render_html(report, base_dir=fail_job.job_dir)
    assert HOSTILE not in page and "<script>alert(2)" not in page
    assert "&lt;img src=x onerror=alert(1)&gt;" in page
    assert parse(page).tags.get("img", 0) == 3  # only the three evidence stills


def test_structure_headings_tables_and_text_alternatives(fail_job: AnalysedJob) -> None:
    page = fail_page(fail_job)
    tags = parse(page)
    assert tags.tags.get("h1") == 1 and tags.tags.get("h2", 0) >= 10
    assert page.startswith("<!doctype html>") and '<html lang="en">' in page
    assert 'role="img" aria-label="Bar chart of qualifying flashes' in page
    assert "Flashes per second as a table" in page
    assert "worst violation window" in page and "outlined with a dashed line" in page
    assert "No culprits: attribution was not run (attribute: none)." in page
    assert "No fixes in this report." in page


def test_pass_page(pass_job: AnalysedJob) -> None:
    page = render_html(report_for(pass_job), base_dir=pass_job.job_dir)
    assert "<strong>PASS</strong>" in page and "No violations." in page
    assert "There is no violation window to outline." in page


def test_page_for_a_failed_capture_has_no_charts(tmp_path: Path) -> None:
    report = build_report("file:///x.html", AuditSettings(duration_s=2), None, None, tmp_path,
                          capture_error="TimeoutError: page did not load", captured_at="2026-10-24T12:00:00Z")
    page = render_html(report, base_dir=tmp_path)
    assert "<strong>INCONCLUSIVE</strong>" in page and "<svg" not in page and "<script" not in page
    assert "No evidence frames." in page


def test_shade_bins_split_counts_into_up_to_four_greys() -> None:
    assert shade_bins(0) == []
    assert [(low, high) for low, high, _ in shade_bins(4)] == [(1, 1), (2, 2), (3, 3), (4, 4)]
    assert [(low, high) for low, high, _ in shade_bins(40)] == [(1, 10), (11, 20), (21, 30), (31, 40)]
    assert [(low, high) for low, high, _ in shade_bins(2)] == [(1, 1), (2, 2)]
    greys = [grey for _, _, grey in shade_bins(40)]
    assert all(re.fullmatch(r"#([0-9a-f]{2})\1\1", g) for g in greys)  # greys only, no hue


def test_block_max_keeps_the_hottest_cell() -> None:
    grid = np.zeros((5, 5), dtype=np.uint16)
    grid[4, 4] = 7
    grid[0, 1] = 2
    assert block_max(grid, 2).tolist() == [[2, 0, 0], [0, 0, 0], [0, 0, 7]]


def report_with_fixes(job: AnalysedJob) -> dict[str, object]:
    """The FAIL report with one culprit and four fixes in the four states the page can show."""
    measured = {"verdict": "FAIL", "generalMaxRatePerSec": 4, "redMaxRatePerSec": 0, "qualifyingGeneral": 7,
                "qualifyingRed": 0, "deterministic": True}
    passed = dict(measured, verdict="PASS", generalMaxRatePerSec=2, qualifyingGeneral=4)
    culprit = {"rank": 1, "selector": "#box", "nodeType": "div", "ftId": "ft-3",
               "rect": {"x": 100.0, "y": 0.0, "w": 400.0, "h": 256.0},
               "animation": {"name": "ft-blink", "duration": 250, "keyframes": []},
               "overlap": 0.75, "ablationDelta": None, "inMinimalSet": False}

    def fix(kind: str, css: str, after: dict[str, object] | None, verified: bool) -> dict[str, object]:
        return {"culprit": 1, "kind": kind, "css": css, "rationale": f"{kind} because", "before": measured,
                "after": after, "verified": verified}

    fixes = [fix("rate", "#box { animation-duration: 400ms !important; }", passed, True),
             fix("luminance", "@keyframes ft-fix-1-lum { 0% { background-color: #545454; } } "
                              "#box { animation-name: ft-fix-1-lum !important; }", measured, False),
             fix("red", "#box { filter: saturate(0.6) !important; }", None, False),
             fix("area", "/* not auto-applied: changes layout */ #box { transform: scale(0.437); }", None, False)]
    return report_for(job, culprits=[culprit], fixes=fixes)


def test_culprits_and_fixes_tables_show_measurements_and_badges(fail_job: AnalysedJob) -> None:
    report = report_with_fixes(fail_job)
    from cli.report import validate_report

    validate_report(report)
    page = render_html(report, base_dir=fail_job.job_dir)
    assert "ft-blink, 250 ms" in page and "x 100, y 0, 400x256 px" in page
    for word, glyph in (("Verified", "✓"), ("Not verified", "✗"), ("Not yet verified", "○"), ("Not auto-applied", "–")):
        assert f'<span aria-hidden="true">{glyph}</span> {word}</span>' in page
    assert "general 4/s, red 0/s" in page and "general 2/s, red 0/s" in page and "not measured" in page
    assert "flashtrace fix REPORT --apply N --verify" in page


def test_fix_css_is_shown_but_the_page_declares_no_animation(fail_job: AnalysedJob) -> None:
    page = render_html(report_with_fixes(fail_job), base_dir=fail_job.job_dir)
    # The luminance fix's text is displayed (with '@' as an entity), yet the page source holds no
    # '@keyframes' and no 'animation:' declaration, so nothing on the page can move (SPEC 9.1).
    assert "&#64;keyframes ft-fix-1-lum" in page
    for moving in ("@keyframes", "animation:", "transition:"):
        assert moving not in page
