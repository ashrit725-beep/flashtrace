"""One self-contained HTML page for a FlashTrace report (SPEC sections 8 and 9).

Safety rules this page follows (SPEC 9.1): nothing moves.  Charts are static inline SVG, evidence is
still frames, and the only script swaps which still frame is shown when the reader presses "Previous"
or "Next"; nothing plays by itself and there is no CSS animation or transition anywhere.

Accessibility: semantic headings and tables, every chart has a text alternative, the verdict is a word
and a glyph (never colour alone), buttons are real <button> elements, and every string taken from the
report is HTML-escaped because a page title or selector can contain anything.
"""

from __future__ import annotations

import base64
import html
import math
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np

from cli.report import FIX_NOTE_PREFIX
from engine.constants import MAX_FLASHES_PER_SECOND
from engine.types import heatmap_from_base64

# Verdict word -> (glyph, CSS class, short explanation).  The glyph is a text character, not an emoji.
VERDICT_STYLES: dict[str, tuple[str, str, str]] = {
    "PASS": ("✓", "pass", "no WCAG 2.3.1 violation detected under the tested conditions"),
    "FAIL": ("✗", "fail", "the WCAG 2.3.1 flash thresholds were exceeded"),
    "INCONCLUSIVE": ("?", "inconclusive", "FlashTrace could not reach a verdict"),
}

# Fix state -> (glyph, CSS class, word).  Word and glyph together, so the state never rests on colour.
FIX_BADGES: dict[str, tuple[str, str, str]] = {
    "verified": ("✓", "ok", "Verified"),
    "failed": ("✗", "no", "Not verified"),
    "manual": ("–", "manual", "Not auto-applied"),
    "untried": ("○", "untried", "Not yet verified"),
}

# Heatmap: at most this many squares across, and four sequential greys from light to dark.
HEATMAP_MAX_BLOCKS = 64
HEATMAP_GREYS: tuple[str, ...] = ("#d4d4d4", "#a3a3a3", "#666666", "#262626")

# Timeline chart geometry in SVG units (the SVG scales to the page width).
CHART_W, CHART_H = 720, 260
PLOT_LEFT, PLOT_RIGHT, PLOT_TOP, PLOT_BOTTOM = 56, 560, 16, 204

CSS = """
:root { color-scheme: light; --ink: #1b1b1b; --muted: #4a4a4a; --rule: #c4c4c4; --paper: #ffffff; --focus: #1a4fd6; }
* { box-sizing: border-box; }
body { margin: 0; background: var(--paper); color: var(--ink);
  font: 16px/1.5 system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif; }
main { max-width: 960px; margin: 0 auto; padding: 24px 16px 48px; }
h1 { font-size: 1.75rem; line-height: 1.2; margin: 0 0 4px; }
h2 { font-size: 1.25rem; margin: 36px 0 8px; padding-bottom: 4px; border-bottom: 1px solid var(--rule); }
p { margin: 8px 0; }
.url { color: var(--muted); overflow-wrap: anywhere; margin-top: 0; }
.verdict { display: flex; gap: 14px; align-items: center; margin: 20px 0 8px; padding: 12px 16px; border: 3px solid currentColor; }
.verdict p { margin: 0; font-size: 1.15rem; }
.verdict strong { font-size: 1.4rem; letter-spacing: 0.04em; margin-right: 6px; }
.verdict-pass { color: #0b4a1f; background: #edf6ef; }
.verdict-fail { color: #7a0010; background: #fbecee; border-width: 4px; }
.verdict-inconclusive { color: #553a00; background: #fdf5df; border-style: dashed; }
.glyph { flex: none; display: inline-flex; align-items: center; justify-content: center; width: 2rem; height: 2rem;
  border: 2px solid currentColor; border-radius: 50%; font-weight: 700; font-size: 1.2rem; }
.reason { font-size: 1.05rem; }
.disclaimer { font-weight: 600; }
.table-wrap { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; margin: 8px 0; }
caption { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }
th, td { text-align: left; vertical-align: top; padding: 6px 8px; border-bottom: 1px solid var(--rule); }
th { font-weight: 600; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
code { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 0.9em; overflow-wrap: anywhere; }
.chart { display: block; width: 100%; max-width: 720px; height: auto; }
.heatmap { display: block; width: 100%; max-width: 640px; height: auto; border: 1px solid var(--ink); }
.legend { display: flex; flex-wrap: wrap; gap: 6px 16px; padding: 0; list-style: none; }
.legend li { display: flex; align-items: center; gap: 6px; }
.swatch { display: inline-block; width: 18px; height: 18px; border: 1px solid var(--ink); }
figure { margin: 8px 0; }
figure img { display: block; max-width: 100%; height: auto; border: 1px solid var(--ink); }
figcaption { color: var(--muted); margin-top: 4px; }
.ev-nav { display: flex; flex-wrap: wrap; gap: 12px; align-items: center; }
button { font: inherit; padding: 6px 14px; color: var(--ink); background: var(--paper); border: 1px solid var(--ink); border-radius: 4px; cursor: pointer; }
button:focus-visible, a:focus-visible, summary:focus-visible { outline: 3px solid var(--focus); outline-offset: 2px; }
.muted { color: var(--muted); }
.badge { display: inline-block; white-space: nowrap; padding: 1px 8px; border: 2px solid currentColor; border-radius: 4px; font-weight: 600; }
.badge-ok { color: #0b4a1f; background: #edf6ef; }
.badge-no { color: #7a0010; background: #fbecee; }
.badge-manual, .badge-untried { color: var(--muted); border-style: dashed; }
.measure { font-size: 0.9em; white-space: nowrap; }
footer { margin-top: 40px; padding-top: 12px; border-top: 1px solid var(--rule); color: var(--muted); }
"""

# The page's only script: show one evidence still at a time and step with the two buttons.
# It never starts anything by itself; until it runs, every still is simply listed.
EVIDENCE_SCRIPT = """
(function () {
  var figures = document.querySelectorAll('#evidence figure');
  var position = document.getElementById('ev-pos');
  var current = 0;
  function show(index) {
    current = (index + figures.length) % figures.length;
    for (var i = 0; i < figures.length; i++) { figures[i].hidden = (i !== current); }
    position.textContent = 'Frame ' + (current + 1) + ' of ' + figures.length;
  }
  document.getElementById('ev-prev').addEventListener('click', function () { show(current - 1); });
  document.getElementById('ev-next').addEventListener('click', function () { show(current + 1); });
  show(0);
})();
"""


def esc(value: object) -> str:
    """HTML-escape any value (quotes too, so it is safe inside attributes)."""
    return html.escape(str(value), quote=True)


def yes_no(flag: object) -> str:
    return "Yes" if flag else "No"


def _table(caption: str, headers: Sequence[str], rows: Sequence[Sequence[str]], numeric: Sequence[int] = ()) -> str:
    """A captioned table.  Cell strings must already be escaped; ``numeric`` columns align right."""
    align = [' class="num"' if i in numeric else "" for i in range(len(headers))]
    head = "".join(f'<th scope="col"{align[i]}>{esc(h)}</th>' for i, h in enumerate(headers))
    body = "".join(
        "<tr>" + "".join(f"<td{align[i]}>{cell}</td>" for i, cell in enumerate(row)) + "</tr>"
        for row in rows
    )
    return (f'<div class="table-wrap"><table><caption>{esc(caption)}</caption>'
            f"<thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>")


def _section(section_id: str, title: str, content: str) -> str:
    return f'<section aria-labelledby="{section_id}"><h2 id="{section_id}">{esc(title)}</h2>{content}</section>'


# --------------------------------------------------------------------------------------- header
def _header(report: Mapping[str, Any]) -> str:
    """Title, URL, verdict banner (word + glyph), reason sentence and the disclaimer."""
    verdict = str(report["verdict"])
    glyph, css_class, meaning = VERDICT_STYLES.get(verdict, ("?", "inconclusive", ""))
    return (
        "<header><h1>FlashTrace report</h1>"
        f'<p class="url">Page: <code>{esc(report["url"])}</code></p></header>'
        f'<div class="verdict verdict-{css_class}"><span class="glyph" aria-hidden="true">{esc(glyph)}</span>'
        f"<p><strong>{esc(verdict)}</strong> {esc(meaning)}</p></div>"
        f'<p class="reason">{esc(report["reason"])}</p>'
        f'<p class="disclaimer">{esc(report["disclaimer"])}</p>'
    )


def _summary(report: Mapping[str, Any]) -> str:
    """Settings and capture facts in one table, with the capture notes folded underneath."""
    settings, capture = report["settings"], report["capture"]
    viewport, window = settings["viewport"], settings["windowPx"]
    rows = [
        ("Capture strategy", esc(capture["strategy"])),
        ("Deterministic capture", yes_no(capture["deterministic"])),
        ("Frames", esc(f'{capture["frameCount"]} ({settings["duration"]:g} s at {settings["fps"]} fps)')),
        ("Frame hash (first 16 hex digits)", f'<code>{esc(str(capture["hash"])[:16] or "none")}</code>'),
        ("Red-flash rule", esc(settings["redRule"])),
        ("Viewport", esc(f'{viewport["width"]}x{viewport["height"]} px')),
        ("Area window", esc(f'{window["width"]}x{window["height"]} px, {settings["cellPx"]} px cells')),
        ("Attribution", esc(settings["attribute"])),
        ("Captured at (UTC)", esc(report["capturedAt"])),
    ]
    if capture.get("fallbackReason"):
        rows.insert(1, ("Why this strategy", esc(capture["fallbackReason"])))
    body = "".join(f'<tr><th scope="row">{esc(name)}</th><td>{value}</td></tr>' for name, value in rows)
    notes = "".join(f"<li>{esc(note)}</li>" for note in capture.get("notes", []))
    folded = f"<details><summary>Capture notes ({len(capture.get('notes', []))})</summary><ul>{notes}</ul></details>" if notes else ""
    table = f'<div class="table-wrap"><table><caption>Settings and capture</caption><tbody>{body}</tbody></table></div>'
    return _section("h-summary", "Settings and capture", table + folded)


# --------------------------------------------------------------------------------------- timeline
def _y_scale(top_value: int) -> tuple[int, int]:
    """Largest value on the y axis and the tick step (always shows the limit line with room above)."""
    y_max = max(MAX_FLASHES_PER_SECOND + 1, top_value)
    step = 1 if y_max <= 8 else math.ceil(y_max / 8)
    return math.ceil(y_max / step) * step, step


def _timeline_axes(n_bins: int, y_max: int, step: int) -> list[str]:
    """Grid lines, tick labels and axis titles."""
    parts: list[str] = []
    height = PLOT_BOTTOM - PLOT_TOP
    for value in range(0, y_max + 1, step):
        y = PLOT_BOTTOM - value / y_max * height
        parts.append(f'<line x1="{PLOT_LEFT}" y1="{y:.1f}" x2="{PLOT_RIGHT}" y2="{y:.1f}" stroke="#e0e0e0"/>')
        parts.append(f'<text x="{PLOT_LEFT - 8}" y="{y + 4:.1f}" text-anchor="end">{value}</text>')
    slot = (PLOT_RIGHT - PLOT_LEFT) / n_bins
    every = max(1, math.ceil(n_bins / 20))
    for k in range(0, n_bins, every):
        x = PLOT_LEFT + (k + 0.5) * slot
        parts.append(f'<text x="{x:.1f}" y="{PLOT_BOTTOM + 18}" text-anchor="middle">{k}</text>')
    parts.append(f'<line x1="{PLOT_LEFT}" y1="{PLOT_BOTTOM}" x2="{PLOT_RIGHT}" y2="{PLOT_BOTTOM}" stroke="#1b1b1b"/>')
    parts.append(f'<line x1="{PLOT_LEFT}" y1="{PLOT_TOP}" x2="{PLOT_LEFT}" y2="{PLOT_BOTTOM}" stroke="#1b1b1b"/>')
    parts.append(f'<text x="{(PLOT_LEFT + PLOT_RIGHT) / 2:.1f}" y="{CHART_H - 12}" text-anchor="middle">'
                 "Second of the capture (bar k covers k to k+1 s)</text>")
    parts.append(f'<text transform="translate(16 {(PLOT_TOP + PLOT_BOTTOM) / 2:.1f}) rotate(-90)" '
                 'text-anchor="middle">Qualifying flashes</text>')
    return parts


def _timeline_bars(timeline: Sequence[Mapping[str, Any]], y_max: int) -> list[str]:
    """Two bars per second: general (solid ink) and red (hatched), so colour is never the only cue."""
    parts: list[str] = []
    slot = (PLOT_RIGHT - PLOT_LEFT) / len(timeline)
    bar = slot * 0.36
    height = PLOT_BOTTOM - PLOT_TOP
    for k, item in enumerate(timeline):
        for offset, key, fill in ((0.12, "generalFlashes", "#1b1b1b"), (0.52, "redFlashes", "url(#ft-hatch)")):
            value = int(item[key])
            if value:
                h = value / y_max * height
                x = PLOT_LEFT + k * slot + offset * slot
                parts.append(f'<rect x="{x:.1f}" y="{PLOT_BOTTOM - h:.1f}" width="{bar:.1f}" height="{h:.1f}" '
                             f'fill="{fill}" stroke="#1b1b1b" stroke-width="0.5"/>')
    return parts


def _timeline_limit_and_legend(y_max: int) -> list[str]:
    """Dashed line at the WCAG limit and a legend in the right margin."""
    # WCAG 2.2 SC 2.3.1: "does not contain anything that flashes more than three times in any one
    # second period" - the line marks 3; a bar above it is over the limit for that second.
    y = PLOT_BOTTOM - MAX_FLASHES_PER_SECOND / y_max * (PLOT_BOTTOM - PLOT_TOP)
    legend_x = PLOT_RIGHT + 16
    return [
        f'<line x1="{PLOT_LEFT}" y1="{y:.1f}" x2="{PLOT_RIGHT}" y2="{y:.1f}" stroke="#7a0010" stroke-width="2" stroke-dasharray="8 5"/>',
        f'<rect x="{legend_x}" y="{PLOT_TOP + 4}" width="14" height="14" fill="#1b1b1b"/>',
        f'<text x="{legend_x + 20}" y="{PLOT_TOP + 16}">general</text>',
        f'<rect x="{legend_x}" y="{PLOT_TOP + 28}" width="14" height="14" fill="url(#ft-hatch)" stroke="#1b1b1b"/>',
        f'<text x="{legend_x + 20}" y="{PLOT_TOP + 40}">red</text>',
        f'<line x1="{legend_x}" y1="{PLOT_TOP + 59}" x2="{legend_x + 14}" y2="{PLOT_TOP + 59}" stroke="#7a0010" stroke-width="2" stroke-dasharray="4 2"/>',
        f'<text x="{legend_x + 20}" y="{PLOT_TOP + 63}">limit: {MAX_FLASHES_PER_SECOND} per second</text>',
    ]


def _timeline_svg(timeline: Sequence[Mapping[str, Any]]) -> str:
    """Static bar chart of qualifying flashes per whole second, by type."""
    top = max([int(t["generalFlashes"]) for t in timeline] + [int(t["redFlashes"]) for t in timeline])
    y_max, step = _y_scale(top)
    general_peak = max(int(t["generalFlashes"]) for t in timeline)
    red_peak = max(int(t["redFlashes"]) for t in timeline)
    label = (f"Bar chart of qualifying flashes in each of {len(timeline)} seconds: general flashes peak at "
             f"{general_peak} and red flashes at {red_peak} in one second; a dashed line marks the limit of "
             f"{MAX_FLASHES_PER_SECOND} per second. The same numbers are in the table below.")
    hatch = ('<defs><pattern id="ft-hatch" width="6" height="6" patternUnits="userSpaceOnUse" '
             'patternTransform="rotate(45)"><rect width="6" height="6" fill="#9b1c1c"/>'
             '<line x1="0" y1="0" x2="0" y2="6" stroke="#ffffff" stroke-width="2"/></pattern></defs>')
    parts = [hatch] + _timeline_axes(len(timeline), y_max, step) + _timeline_bars(timeline, y_max)
    parts += _timeline_limit_and_legend(y_max)
    return (f'<svg class="chart" viewBox="0 0 {CHART_W} {CHART_H}" role="img" aria-label="{esc(label)}" '
            f'font-family="system-ui, sans-serif" font-size="12" fill="#1b1b1b">{"".join(parts)}</svg>')


def _timeline_section(report: Mapping[str, Any]) -> str:
    timeline = report["timeline"]
    if not timeline:
        return _section("h-timeline", "Flashes per second", "<p>No timeline: the engine did not analyse any frames.</p>")
    note = ("<p class=\"muted\">Each bar counts qualifying flashes inside one whole second. The verdict uses a "
            "one-second window that slides frame by frame, so a window that straddles two bars can hold more "
            "flashes than either bar shows.</p>")
    rows = [(esc(f'{float(t["t"]):g}'), esc(t["generalFlashes"]), esc(t["redFlashes"])) for t in timeline]
    table = _table("Flashes per second as a table", ("Second starting at (s)", "General", "Red"), rows, numeric=(1, 2))
    return _section("h-timeline", "Flashes per second", _timeline_svg(timeline) + note + table)


# --------------------------------------------------------------------------------------- findings
def _violations_section(report: Mapping[str, Any]) -> str:
    violations = report["violations"]
    if not violations:
        return _section("h-violations", "Violations", "<p>No violations.</p>")
    fps = int(report["settings"]["fps"])
    rows = []
    for v in violations:
        w = v["window"]
        rows.append((
            esc(v["type"]),
            esc(f'{v["start"]}-{v["end"]}'),
            esc(f'{v["start"] / fps:.3f}-{(v["end"] + 1) / fps:.3f}'),
            esc(f'{v["maxRatePerSec"]:g}'),
            esc(f'x {w["x"]:g}, y {w["y"]:g}, {w["w"]:g}x{w["h"]:g} px'),
            esc(len(v["frameIndices"])),
        ))
    headers = ("Type", "Frames", "Time (s)", "Highest flashes in one second", "Window", "Qualifying flashes")
    return _section("h-violations", "Violations", _table("Violations", headers, rows, numeric=(3, 5)))


def _advisory_section(report: Mapping[str, Any]) -> str:
    items = report["advisory"]
    if not items:
        return _section("h-advisory", "Advisories", "<p>No advisories.</p>")
    fps = int(report["settings"]["fps"])
    lines = "".join(f"<li>{esc(_advisory_sentence(a, fps))}</li>" for a in items)
    return _section("h-advisory", "Advisories", f"<ul>{lines}</ul>")


def _advisory_sentence(advisory: Mapping[str, Any], fps: int) -> str:
    start, end = int(advisory["start"]), int(advisory["end"])
    return (f'{advisory["label"]}: {advisory["flashType"]} flashes averaged {float(advisory["meanRatePerSec"]):.2f} '
            f"per second from frame {start} to {end} ({start / fps:.2f}-{(end + 1) / fps:.2f} s).")


# --------------------------------------------------------------------------------------- evidence
def _image_source(png_path: str, embed: bool, base: Path) -> str | None:
    """A data: URI of the PNG (embed) or the relative path; None when the file to embed is missing."""
    if not embed:
        return png_path
    file = base / png_path
    if not file.is_file():
        return None
    return "data:image/png;base64," + base64.b64encode(file.read_bytes()).decode("ascii")


def _evidence_section(report: Mapping[str, Any], embed: bool, base: Path) -> str:
    frames = report["evidenceFrames"]
    if not frames:
        return _section("h-evidence", "Evidence frames", "<p>No evidence frames.</p>")
    viewport = report["settings"]["viewport"]
    figures = []
    for frame in frames:
        source = _image_source(str(frame["pngPath"]), embed, base)
        picture = (f'<img src="{esc(source)}" alt="{esc(frame["label"])}" width="{int(viewport["width"])}" '
                   f'height="{int(viewport["height"])}">' if source else
                   f'<p>Frame file not found: <code>{esc(frame["pngPath"])}</code></p>')
        figures.append(f"<figure>{picture}<figcaption>{esc(frame['label'])}</figcaption></figure>")
    intro = ("<p>Still frames from the capture. Use the buttons to step from one still to the next; "
             "nothing plays automatically.</p>")
    nav = ('<div class="ev-nav"><button type="button" id="ev-prev">Previous frame</button>'
           f'<span id="ev-pos" aria-live="polite">{len(frames)} frames</span>'
           '<button type="button" id="ev-next">Next frame</button></div>')
    return _section("h-evidence", "Evidence frames", intro + nav + f'<div id="evidence">{"".join(figures)}</div>')


# --------------------------------------------------------------------------------------- heatmap
def block_max(grid: np.ndarray, block: int) -> np.ndarray:
    """Largest value inside each ``block`` x ``block`` square (the grid is zero-padded to fit)."""
    h, w = grid.shape
    padded = np.zeros((-(-h // block) * block, -(-w // block) * block), dtype=np.int64)
    padded[:h, :w] = grid
    return padded.reshape(padded.shape[0] // block, block, padded.shape[1] // block, block).max(axis=(1, 3))


def shade_bins(max_count: int) -> list[tuple[int, int, str]]:
    """Split counts 1..max_count into up to four ranges, each with a grey (darker = more flashes)."""
    if max_count < 1:
        return []
    k = min(len(HEATMAP_GREYS), max_count)
    greys = HEATMAP_GREYS[len(HEATMAP_GREYS) - k:]
    return [(1 + (max_count * i) // k, (max_count * (i + 1)) // k, greys[i]) for i in range(k)]


def _shade_for(value: int, bins: Sequence[tuple[int, int, str]]) -> str:
    for low, high, grey in bins:
        if low <= value <= high:
            return grey
    return bins[-1][2]


def worst_violation(report: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """Highest rate first, then the earliest (the same choice the engine's reason sentence makes)."""
    violations = report["violations"]
    if not violations:
        return None
    return max(violations, key=lambda v: (v["maxRatePerSec"], -v["start"], 1 if v["type"] == "general" else 0))


def _heatmap_svg(grid: np.ndarray, cell: int, worst: Mapping[str, Any] | None) -> tuple[str, list[tuple[int, int, str]]]:
    block = max(1, math.ceil(max(grid.shape) / HEATMAP_MAX_BLOCKS))
    blocks = block_max(grid, block)
    bins = shade_bins(int(blocks.max()))
    size = block * cell
    parts = []
    for by, bx in zip(*np.nonzero(blocks)):
        parts.append(f'<rect x="{bx * size}" y="{by * size}" width="{size}" height="{size}" '
                     f'fill="{_shade_for(int(blocks[by, bx]), bins)}"/>')
    if worst is not None:
        w = worst["window"]
        box = f'x="{w["x"]:g}" y="{w["y"]:g}" width="{w["w"]:g}" height="{w["h"]:g}" fill="none"'
        parts.append(f'<rect {box} stroke="#ffffff" stroke-width="8"/>')
        parts.append(f'<rect {box} stroke="#b3001b" stroke-width="4" stroke-dasharray="14 8"/>')
        label_y = w["y"] + 28 if w["y"] < 32 else w["y"] - 10
        parts.append(f'<text x="{w["x"] + 6:g}" y="{label_y:g}" font-size="22" stroke="#ffffff" stroke-width="5" '
                     'paint-order="stroke" fill="#1b1b1b">worst violation window</text>')
    width_px, height_px = grid.shape[1] * cell, grid.shape[0] * cell
    label = "Heatmap of where flashes happened; darker squares completed more flashes. Summary in the text below."
    svg = (f'<svg class="heatmap" viewBox="0 0 {width_px} {height_px}" role="img" aria-label="{esc(label)}" '
           f'font-family="system-ui, sans-serif" shape-rendering="crispEdges"><rect width="{width_px}" height="{height_px}" fill="#ffffff"/>'
           f'{"".join(parts)}</svg>')
    return svg, bins


def _heatmap_text(grid: np.ndarray, cell: int, worst: Mapping[str, Any] | None) -> str:
    active = int(np.count_nonzero(grid))
    text = (f"{active} of {grid.size} cells ({cell}x{cell} px each) completed at least one flash; "
            f"the busiest cell completed {int(grid.max()) if grid.size else 0}.")
    if worst is None:
        return text + " There is no violation window to outline."
    w = worst["window"]
    return text + (f' The worst violation window ({worst["type"]}, {worst["maxRatePerSec"]:g} flashes in one second) '
                   f'is outlined with a dashed line at x {w["x"]:g}, y {w["y"]:g}, {w["w"]:g}x{w["h"]:g} px.')


def _heatmap_section(report: Mapping[str, Any]) -> str:
    data = report["heatmap"]
    if not data["w"] or not data["h"]:
        return _section("h-heatmap", "Where the flashes were", "<p>No heatmap: the engine did not analyse any frames.</p>")
    grid = heatmap_from_base64(data["data"], int(data["w"]), int(data["h"])).astype(np.int64)
    worst = worst_violation(report)
    svg, bins = _heatmap_svg(grid, int(data["cellSize"]), worst)
    legend = "".join(f'<li><span class="swatch" style="background:{grey}"></span>{low if low == high else f"{low}-{high}"} '
                     "flashes</li>" for low, high, grey in bins)
    legend_html = (f'<ul class="legend" aria-label="Heatmap legend"><li><span class="swatch" style="background:#ffffff">'
                   f"</span>0 flashes</li>{legend}</ul>")
    note = "<p class=\"muted\">Each square shows the highest count among the cells it covers.</p>"
    return _section("h-heatmap", "Where the flashes were",
                    svg + legend_html + note + f"<p>{esc(_heatmap_text(grid, int(data['cellSize']), worst))}</p>")


# --------------------------------------------------------------------------------------- culprits and fixes
def css_code(css: str) -> str:
    """A fix's CSS as escaped <code>.  '@' is written as the entity &#64; (it displays the same), so the
    page's source never contains '@keyframes': a plain-text check can then still prove that the page
    itself declares no animation (SPEC 9.1)."""
    return f"<code>{esc(css).replace('@', '&#64;')}</code>"


def _animation_text(animation: Mapping[str, Any] | None) -> str:
    """'ft-blink, 250 ms' / 'unnamed, duration unknown' / 'none'."""
    if not animation:
        return "none"
    name = str(animation.get("name") or "unnamed")
    duration = animation.get("duration")
    timing = f"{float(duration):g} ms" if isinstance(duration, (int, float)) else "duration unknown"
    return f"{name}, {timing}"


def _culprits_section(report: Mapping[str, Any]) -> str:
    culprits = report["culprits"]
    if not culprits:
        why = ("attribution was not run (attribute: none)" if report["settings"]["attribute"] == "none"
               else "no element was found responsible")
        return _section("h-culprits", "Likely causes", f"<p>No culprits: {esc(why)}.</p>")
    rows = [(
        esc(c["rank"]), f'<code>{esc(c["selector"])}</code>', esc(c["nodeType"]),
        esc(_animation_text(c.get("animation"))),
        esc(f'x {c["rect"]["x"]:g}, y {c["rect"]["y"]:g}, {c["rect"]["w"]:g}x{c["rect"]["h"]:g} px'),
        esc(f'{float(c["overlap"]):.2f}'),
        esc("not run" if c["ablationDelta"] is None else f'{float(c["ablationDelta"]):g}'),
        yes_no(c["inMinimalSet"]),
    ) for c in culprits]
    headers = ("Rank", "Selector", "Element", "Animation", "Box", "Overlap", "Flashes removed when suppressed",
               "In minimal set")
    return _section("h-culprits", "Likely causes", _table("Culprits", headers, rows, numeric=(0, 5, 6)))


def fix_state(fix: Mapping[str, Any]) -> str:
    """'verified', 'failed' (re-captured, did not pass), 'manual' (area: never applied) or 'untried'."""
    if fix["verified"]:
        return "verified"
    if fix["kind"] == "area":
        return "manual"
    return "failed" if fix["after"] is not None else "untried"


def fix_badge(fix: Mapping[str, Any]) -> str:
    glyph, css_class, word = FIX_BADGES[fix_state(fix)]
    return f'<span class="badge badge-{css_class}"><span aria-hidden="true">{esc(glyph)}</span> {esc(word)}</span>'


def measurement_html(measured: Mapping[str, Any] | None) -> str:
    """A before/after measurement as a few short lines (verdict first)."""
    if measured is None:
        return '<span class="muted">not measured</span>'
    lines = [
        f'<strong>{esc(measured["verdict"])}</strong>',
        esc(f'general {measured["generalMaxRatePerSec"]:g}/s, red {measured["redMaxRatePerSec"]:g}/s'),
        esc(f'qualifying: {measured["qualifyingGeneral"]} general, {measured["qualifyingRed"]} red'),
        esc(f'deterministic: {"yes" if measured["deterministic"] else "no"}'),
    ]
    return f'<span class="measure">{"<br>".join(lines)}</span>'


def _fixes_section(report: Mapping[str, Any]) -> str:
    fixes = report["fixes"]
    if not fixes:
        stated = [note[len(FIX_NOTE_PREFIX):] for note in report["limitations"] if note.startswith(FIX_NOTE_PREFIX)]
        why = f": {esc(stated[0])}" if stated else "."
        return _section("h-fixes", "Suggested fixes", f"<p>No fixes in this report{why}</p>")
    rows = [(esc(number), esc(f["culprit"]), esc(f["kind"]), css_code(f["css"]), esc(f["rationale"]),
             measurement_html(f["before"]), measurement_html(f["after"]), fix_badge(f))
            for number, f in enumerate(fixes, start=1)]
    headers = ("Fix", "Culprit rank", "Kind", "CSS", "Why", "Before", "After (re-capture with the fix)",
               "Verified")
    intro = ("<p>Each fix is a CSS override derived from this audit's measurements. \"Verified\" means the page was "
             "captured again with that fix injected and FlashTrace found no 2.3.1 violation under deterministic "
             "capture; verify a fix with <code>flashtrace fix REPORT --apply N --verify</code>.</p>")
    return _section("h-fixes", "Suggested fixes", intro + _table("Fixes", headers, rows, numeric=(0, 1)))


def _list_section(section_id: str, title: str, items: Sequence[str]) -> str:
    if not items:
        return _section(section_id, title, "<p>None.</p>")
    return _section(section_id, title, "<ul>" + "".join(f"<li>{esc(item)}</li>" for item in items) + "</ul>")


def _footer(report: Mapping[str, Any]) -> str:
    return (f"<footer><p>FlashTrace engine {esc(report['engineVersion'])}, report format {esc(report['version'])}, "
            f"captured {esc(report['capturedAt'])}.</p><p class=\"disclaimer\">{esc(report['disclaimer'])}</p></footer>")


# --------------------------------------------------------------------------------------- page
def render_html(report: Mapping[str, Any], embed_frames: bool = True, base_dir: str | Path | None = None) -> str:
    """The whole report as one HTML document.

    ``embed_frames`` puts the evidence PNGs inside the page as base64 data URIs (read from ``base_dir``,
    the folder report.json's ``pngPath`` values are relative to; default: the current directory).
    Otherwise the images point at those relative paths.
    """
    base = Path(base_dir) if base_dir is not None else Path.cwd()
    sections = [
        _header(report),
        _summary(report),
        _timeline_section(report),
        _violations_section(report),
        _advisory_section(report),
        _evidence_section(report, embed_frames, base),
        _heatmap_section(report),
        _culprits_section(report),
        _fixes_section(report),
        _list_section("h-limitations", "Limitations", report["limitations"]),
        _list_section("h-assumptions", "Assumptions", report["assumptions"]),
        _footer(report),
    ]
    script = f"<script>{EVIDENCE_SCRIPT}</script>" if report["evidenceFrames"] else ""
    title = f"FlashTrace report: {report['verdict']} - {report['url']}"
    return (
        '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{esc(title)}</title>\n<style>{CSS}</style>\n</head>\n<body>\n<main>\n"
        + "\n".join(sections)
        + f"\n</main>\n{script}\n</body>\n</html>\n"
    )
